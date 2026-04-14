#!/usr/bin/env python3
"""
Biomedical literature search script using PubMed with Europe PMC enrichment.
"""

import argparse
import html
import json
import logging
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import requests
import yaml


logger = logging.getLogger(__name__)

PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EUROPEPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def get_vault_path(config: Optional[Dict] = None, cli_vault: Optional[str] = None) -> str:
    if cli_vault:
        return cli_vault
    env_path = os.environ.get("OBSIDIAN_VAULT_PATH")
    if env_path:
        return env_path
    if config and config.get("vault_path"):
        return config["vault_path"]
    raise ValueError("Vault path is required via --vault, OBSIDIAN_VAULT_PATH, or config")


def load_config(config_path: str) -> Dict:
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    config.setdefault("language", "en")
    config.setdefault("lookback_days", 90)
    config.setdefault("top_n", 10)
    config.setdefault("max_results", 50)
    config.setdefault("keywords", [])
    config.setdefault("excluded_keywords", [])
    config.setdefault("article_types", [])
    return config


def normalize_text(text: Optional[str]) -> str:
    if text is None:
        return ""
    normalized = html.unescape(str(text))
    normalized = unicodedata.normalize("NFC", normalized)
    normalized = normalized.replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_title(title: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", normalize_text(title).lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _paper_quality_key(paper: Dict) -> tuple:
    return (
        1 if paper.get("abstract") else 0,
        len(paper.get("abstract", "")),
        1 if paper.get("pmcid") else 0,
        1 if paper.get("doi") else 0,
        1 if paper.get("is_open_access") else 0,
        len(paper.get("authors", [])),
    )


def deduplicate_papers(papers: List[Dict]) -> List[Dict]:
    by_pmid: Dict[str, Dict] = {}
    by_doi: Dict[str, Dict] = {}
    by_title: Dict[str, Dict] = {}

    for paper in papers:
        pmid = (paper.get("pmid") or "").strip()
        doi = (paper.get("doi") or "").strip().lower()
        title_key = normalize_title(paper.get("title", ""))

        if pmid:
            existing = by_pmid.get(pmid)
            if not existing or _paper_quality_key(paper) > _paper_quality_key(existing):
                by_pmid[pmid] = paper
            continue

        if doi:
            existing = by_doi.get(doi)
            if not existing or _paper_quality_key(paper) > _paper_quality_key(existing):
                by_doi[doi] = paper
            continue

        if title_key:
            existing = by_title.get(title_key)
            if not existing or _paper_quality_key(paper) > _paper_quality_key(existing):
                by_title[title_key] = paper

    return list(by_pmid.values()) + list(by_doi.values()) + list(by_title.values())


def compute_relevance(text: str, keywords: List[str]) -> float:
    text_lower = (text or "").lower()
    score = 0.0
    for keyword in keywords:
        keyword_lower = keyword.lower()
        if keyword_lower in text_lower:
            score += 2.0 if keyword_lower in (text or "").lower() else 1.0
    return score


def parse_publication_date(value: str) -> Optional[datetime]:
    if not value:
        return None

    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y %b %d", "%Y %b", "%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    match = re.match(r"^(\d{4})\s+([A-Za-z]{3})", text)
    if match:
        try:
            return datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y %b")
        except ValueError:
            return None

    return None


def format_pubmed_xml_date(year: str, month: str = "", day: str = "") -> str:
    month_value = (month or "").strip()
    day_value = (day or "").strip()

    if month_value.isdigit():
        month_norm = month_value.zfill(2)
    elif month_value:
        try:
            month_norm = datetime.strptime(month_value[:3], "%b").strftime("%m")
        except ValueError:
            month_norm = ""
    else:
        month_norm = ""

    if day_value and day_value.isdigit():
        day_norm = day_value.zfill(2)
    else:
        day_norm = ""

    if year and month_norm and day_norm:
        return f"{year}-{month_norm}-{day_norm}"
    if year and month_norm:
        return f"{year}-{month_norm}"
    return year or ""


def format_pubmed_display_date(value: str) -> str:
    parsed = parse_publication_date(value)
    if not parsed:
        return value or "N/A"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value or ""):
        return f"{parsed.year} {parsed.strftime('%b')} {parsed.day}"
    if re.match(r"^\d{4}-\d{2}$", value or ""):
        return f"{parsed.year} {parsed.strftime('%b')}"
    return value


def compute_recency(publication_date: str, current_date: str) -> float:
    if not publication_date:
        return 0.0
    try:
        published = parse_publication_date(publication_date)
        current = datetime.fromisoformat(current_date[:10])
        if not published:
            return 0.0
    except ValueError:
        return 0.0
    days = max((current - published).days, 0)
    if days <= 30:
        return 3.0
    if days <= 90:
        return 2.0
    if days <= 365:
        return 1.0
    return 0.5


def score_paper(paper: Dict, config: Dict, current_date: Optional[str] = None) -> Dict:
    current_date = current_date or datetime.now().strftime("%Y-%m-%d")
    title = paper.get("title", "")
    abstract = paper.get("abstract", "")
    keywords = config.get("keywords", [])
    preferred_types = [t.lower() for t in config.get("article_types", [])]
    article_types = [t.lower() for t in paper.get("article_types", [])]

    relevance = compute_relevance(f"{title}\n{abstract}", keywords)
    recency = compute_recency(paper.get("publication_date", ""), current_date)
    metadata = 1.0 if abstract else 0.0
    metadata += 0.5 if (paper.get("pmid") or paper.get("doi")) else 0.0
    access = 1.0 if paper.get("is_open_access") or paper.get("pmcid") else 0.0
    article_type = 1.0 if any(t in article_types for t in preferred_types) else 0.0
    total = round(relevance + recency + metadata + access + article_type, 2)

    return {
        "relevance": round(relevance, 2),
        "recency": round(recency, 2),
        "metadata": round(metadata, 2),
        "access": round(access, 2),
        "article_type": round(article_type, 2),
        "total": total,
    }


def build_links(paper: Dict) -> str:
    links = []
    if paper.get("pmid"):
        links.append(f"[PubMed](https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/)")
        links.append(f"[Europe PMC](https://europepmc.org/article/MED/{paper['pmid']})")
    if paper.get("doi"):
        links.append(f"[DOI](https://doi.org/{paper['doi']})")
    return " | ".join(links) if links else "N/A"


def build_markdown_note(
    papers: List[Dict],
    config: Dict,
    target_date: str,
    lookback_days: int,
    total_found: int,
    total_filtered: int,
) -> str:
    keywords = config.get("keywords", [])
    lines = [
        "---",
        "tags:",
        "  - llm-generated",
        "  - biomed-paper-recommend",
        "keywords:",
    ]
    for keyword in keywords:
        lines.append(f"  - {keyword}")
    lines.extend(
        [
            "---",
            "",
            "# Biomedical Paper Recommendations",
            "",
            "## Overview",
            "",
            f"- Query theme: {', '.join(keywords) if keywords else 'N/A'}",
            f"- Search window: last {lookback_days} days",
            f"- Total candidates: {total_found}",
            f"- Total recommended: {total_filtered}",
            "",
            "## Recommended Papers",
            "",
        ]
    )

    if not papers:
        lines.append("No papers matched the current biomedical query and filters.")
        return "\n".join(lines) + "\n"

    for paper in papers:
        access = "Open access" if paper.get("is_open_access") or paper.get("pmcid") else "Abstract only"
        authors = ", ".join(paper.get("authors", [])) or "N/A"
        ids = " / ".join(
            part for part in [
                f"PMID {paper['pmid']}" if paper.get("pmid") else "",
                f"PMCID {paper['pmcid']}" if paper.get("pmcid") else "",
                f"DOI {paper['doi']}" if paper.get("doi") else "",
            ] if part
        ) or "N/A"
        lines.extend(
            [
                f"### {paper.get('title', 'Untitled')}",
                f"- Authors: {authors}",
                f"- Journal: {paper.get('journal', 'N/A')}",
                f"- Date: {format_pubmed_display_date(paper.get('publication_date', 'N/A'))}{' (Epub)' if paper.get('publication_date_source') == 'electronic' else ''}",
                f"- Issue date: {format_pubmed_display_date(paper.get('issue_publication_date', 'N/A'))}" if paper.get("issue_publication_date") else "",
                f"- IDs: {ids}",
                f"- Access: {access}",
                f"- Links: {build_links(paper)}",
                "",
                paper.get("summary_line") or "No summary available.",
                "",
            ]
        )

    return "\n".join(lines) + "\n"


def serialize_result_for_stdout(payload: Dict, encoding: Optional[str] = None) -> str:
    encoding = (encoding or getattr(sys.stdout, "encoding", None) or "utf-8").lower()
    ensure_ascii = encoding in {"gbk", "cp936"}
    return json.dumps(payload, ensure_ascii=ensure_ascii, indent=2)


def pubmed_search(query: str, lookback_days: int, max_results: int) -> List[str]:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=lookback_days)
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": max_results,
        "sort": "pub date",
        "mindate": start_date.strftime("%Y/%m/%d"),
        "maxdate": end_date.strftime("%Y/%m/%d"),
        "datetype": "pdat",
    }
    response = requests.get(PUBMED_ESEARCH_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("esearchresult", {}).get("idlist", [])


def pubmed_summary(pmids: List[str]) -> List[Dict]:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "json",
    }
    response = requests.get(PUBMED_ESUMMARY_URL, params=params, timeout=30)
    response.raise_for_status()
    result = response.json().get("result", {})
    papers = []
    for pmid in result.get("uids", []):
        item = result.get(pmid, {})
        papers.append(
            {
                "pmid": pmid,
                "title": clean_html_text(item.get("title", "")),
                "authors": [
                    normalize_text(a.get("name", ""))
                    for a in item.get("authors", [])
                    if a.get("name")
                ],
                "journal": clean_html_text(item.get("fulljournalname", "") or item.get("source", "")),
                "publication_date": item.get("pubdate", "")[:10],
                "issue_publication_date": item.get("pubdate", "")[:10],
                "publication_date_source": "issue",
                "article_types": [normalize_text(value) for value in item.get("pubtype", [])],
                "source": "pubmed",
            }
        )
    return papers


def extract_pubmed_article_details(article_xml: str) -> Dict[str, str]:
    wrapped_xml = f"<Root>{article_xml}</Root>"
    root = ET.fromstring(wrapped_xml)
    article = root.find("./PubmedArticle")
    if article is None:
        article = root

    pmid = (article.findtext(".//PMID") or "").strip()
    abstract_parts = []
    for elem in article.findall(".//AbstractText"):
        part = "".join(elem.itertext())
        part = normalize_text(part)
        if part:
            abstract_parts.append(part)
    abstract = " ".join(abstract_parts)

    issue_pubdate = article.find(".//JournalIssue/PubDate")
    issue_date = ""
    if issue_pubdate is not None:
        issue_date = format_pubmed_xml_date(
            issue_pubdate.findtext("Year", default=""),
            issue_pubdate.findtext("Month", default=""),
            issue_pubdate.findtext("Day", default=""),
        )

    electronic_date = None
    for article_date in article.findall(".//ArticleDate"):
        if (article_date.attrib.get("DateType") or "").lower() == "electronic":
            electronic_date = article_date
            break

    if electronic_date is not None:
        publication_date = format_pubmed_xml_date(
            electronic_date.findtext("Year", default=""),
            electronic_date.findtext("Month", default=""),
            electronic_date.findtext("Day", default=""),
        )
        publication_date_source = "electronic"
    else:
        publication_date = issue_date
        publication_date_source = "issue"

    return {
        "pmid": pmid,
        "abstract": abstract,
        "publication_date": publication_date,
        "publication_date_source": publication_date_source,
        "issue_publication_date": issue_date,
    }


def pubmed_abstracts(pmids: List[str]) -> Dict[str, Dict[str, str]]:
    if not pmids:
        return {}
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    response = requests.get(PUBMED_EFETCH_URL, params=params, timeout=30)
    response.raise_for_status()
    xml_text = response.text
    records: Dict[str, Dict[str, str]] = {}
    articles = re.findall(r"<PubmedArticle>(.*?)</PubmedArticle>", xml_text, re.DOTALL)
    for article in articles:
        details = extract_pubmed_article_details(article)
        if details.get("pmid"):
            records[details["pmid"]] = details
    return records


def enrich_with_europepmc(paper: Dict) -> Dict:
    pmid = paper.get("pmid")
    if not pmid:
        return paper
    params = {
        "query": f"EXT_ID:{pmid} AND SRC:MED",
        "format": "json",
        "pageSize": 1,
        "resultType": "core",
    }
    try:
        response = requests.get(EUROPEPMC_SEARCH_URL, params=params, timeout=30)
        response.raise_for_status()
        records = response.json().get("resultList", {}).get("result", [])
    except requests.RequestException as exc:
        logger.warning("Europe PMC enrichment failed for PMID %s: %s", pmid, exc)
        return paper

    if not records:
        return paper

    record = records[0]
    paper["pmcid"] = normalize_text(record.get("pmcid", "") or paper.get("pmcid", ""))
    paper["doi"] = normalize_text(record.get("doi", "") or paper.get("doi", ""))
    paper["abstract"] = clean_html_text(record.get("abstractText", "") or paper.get("abstract", ""))
    paper["title"] = clean_html_text(record.get("title", "") or paper.get("title", ""))
    paper["journal"] = clean_html_text(record.get("journalTitle", "") or paper.get("journal", ""))
    paper["is_open_access"] = bool(record.get("isOpenAccess") == "Y" or record.get("pmcid"))
    paper["source"] = "pubmed+europepmc"
    return paper


def filter_papers(papers: List[Dict], config: Dict) -> List[Dict]:
    excluded = [kw.lower() for kw in config.get("excluded_keywords", [])]
    filtered = []
    for paper in papers:
        text = f"{paper.get('title', '')}\n{paper.get('abstract', '')}".lower()
        if any(term in text for term in excluded):
            continue
        filtered.append(paper)
    return filtered


def clean_html_text(text: str) -> str:
    if not text:
        return ""
    cleaned = normalize_text(text)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return normalize_text(cleaned)


def summarize_paper(paper: Dict) -> str:
    abstract = clean_html_text((paper.get("abstract") or "").strip())
    if not abstract:
        return "No abstract available."
    first_sentence = re.split(r"(?<=[.!?])\s+", abstract)[0].strip()
    return first_sentence or "No abstract available."


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_search(config: Dict, vault: str, output: str, target_date: Optional[str] = None) -> Dict:
    target_date = target_date or datetime.now().strftime("%Y-%m-%d")
    keywords = config.get("keywords", [])
    query = " OR ".join(f'"{keyword}"' for keyword in keywords) if keywords else "biomedical"
    pmids = pubmed_search(query, config["lookback_days"], config["max_results"])
    papers = pubmed_summary(pmids)
    abstracts = pubmed_abstracts(pmids)

    for paper in papers:
        abstract_details = abstracts.get(paper.get("pmid", ""), {})
        paper["abstract"] = abstract_details.get("abstract", "")
        if abstract_details.get("publication_date"):
            paper["publication_date"] = abstract_details["publication_date"]
            paper["publication_date_source"] = abstract_details.get("publication_date_source", "issue")
        if abstract_details.get("issue_publication_date"):
            paper["issue_publication_date"] = abstract_details["issue_publication_date"]
        paper = enrich_with_europepmc(paper)
        paper["summary_line"] = summarize_paper(paper)

    total_found = len(papers)
    papers = filter_papers(papers, config)
    papers = deduplicate_papers(papers)

    for paper in papers:
        paper["scores"] = score_paper(paper, config, current_date=target_date)

    papers.sort(key=lambda item: item["scores"]["total"], reverse=True)
    top_papers = papers[: config["top_n"]]

    result = {
        "query": query,
        "total_found": total_found,
        "total_filtered": len(top_papers),
        "top_papers": top_papers,
    }

    output_path = Path(output)
    write_text(output_path, json.dumps(result, ensure_ascii=False, indent=2))

    note = build_markdown_note(
        papers=top_papers,
        config=config,
        target_date=target_date,
        lookback_days=config["lookback_days"],
        total_found=total_found,
        total_filtered=len(top_papers),
    )
    note_name = f"{target_date}-biomed-papers.md"
    note_path = Path(vault) / "10_Daily" / note_name
    write_text(note_path, note)
    result["note_path"] = str(note_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Search biomedical papers via PubMed and Europe PMC")
    parser.add_argument(
        "--config",
        type=str,
        default=str(Path(__file__).resolve().parents[1] / "biomed-papers.yaml"),
        help="Path to biomed-papers.yaml",
    )
    parser.add_argument("--vault", type=str, default=None, help="Obsidian vault path")
    parser.add_argument("--output", type=str, default="biomed_papers_filtered.json", help="Output JSON path")
    parser.add_argument("--target-date", type=str, default=None, help="Target date in YYYY-MM-DD format")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    config = load_config(args.config)
    try:
        vault = get_vault_path(config, args.vault)
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    result = run_search(config, vault=vault, output=args.output, target_date=args.target_date)
    print(serialize_result_for_stdout(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
