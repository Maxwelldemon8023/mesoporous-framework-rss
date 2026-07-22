#!/usr/bin/env python3
"""Build a curated RSS feed for mesoporous conductive framework literature.

Pure Python 3.7+ standard library; no API key is required for the default sources.
"""

import argparse
import calendar
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


USER_AGENT = "MesoporousFrameworkRSS/0.1 (academic literature monitor)"


def utcnow():
    return dt.datetime.utcnow().replace(microsecond=0)


def iso_date(value):
    if not value:
        return ""
    return str(value)[:10]


def parse_date(value):
    value = iso_date(value)
    for date_format in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return dt.datetime.strptime(value, date_format)
        except (TypeError, ValueError):
            pass
    return dt.datetime(1970, 1, 1)


def strip_tags(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def normalized_title(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def stable_id(paper):
    if paper.get("doi"):
        return "doi:" + paper["doi"].lower().replace("https://doi.org/", "")
    if paper.get("arxiv_id"):
        return "arxiv:" + paper["arxiv_id"].lower()
    if paper.get("openalex_id"):
        return "openalex:" + paper["openalex_id"].rsplit("/", 1)[-1].lower()
    return "title:" + hashlib.sha1(normalized_title(paper.get("title")).encode("utf-8")).hexdigest()


def http_get_json(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def http_get_xml(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return ET.fromstring(response.read())


def reconstruct_abstract(inverted):
    if not inverted:
        return ""
    positioned = []
    for word, positions in inverted.items():
        for position in positions:
            positioned.append((position, word))
    return " ".join(word for _, word in sorted(positioned))


def search_openalex(query, since, limit, config):
    params = {
        "search": query,
        "filter": "from_publication_date:" + since,
        "per-page": min(limit, 100),
        "select": "id,doi,title,publication_date,primary_location,authorships,abstract_inverted_index"
    }
    mailto = config["search"].get("mailto", "").strip()
    if mailto:
        params["mailto"] = mailto
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = http_get_json(url, config["search"]["timeout_seconds"])
    papers = []
    for item in data.get("results", []):
        location = item.get("primary_location") or {}
        source = location.get("source") or {}
        authors = []
        institutions = []
        for authorship in item.get("authorships") or []:
            author = authorship.get("author") or {}
            if author.get("display_name"):
                authors.append(author["display_name"])
            institutions.extend(i.get("display_name", "") for i in authorship.get("institutions") or [])
        doi = (item.get("doi") or "").replace("https://doi.org/", "")
        papers.append({
            "title": item.get("title") or "Untitled",
            "abstract": reconstruct_abstract(item.get("abstract_inverted_index")),
            "authors": authors,
            "institutions": [x for x in institutions if x],
            "published": item.get("publication_date") or "",
            "journal": source.get("display_name") or "OpenAlex",
            "doi": doi,
            "openalex_id": item.get("id") or "",
            "url": ("https://doi.org/" + doi) if doi else (item.get("id") or ""),
            "sources": ["OpenAlex"]
        })
    return papers


def search_crossref(query, since, limit, config):
    params = {
        "query.title": query,
        "filter": "from-pub-date:" + since,
        "rows": min(limit, 100),
        "select": "DOI,title,abstract,author,published-online,published-print,container-title,URL"
    }
    mailto = config["search"].get("mailto", "").strip()
    if mailto:
        params["mailto"] = mailto
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = http_get_json(url, config["search"]["timeout_seconds"])
    papers = []
    for item in data.get("message", {}).get("items", []):
        date_parts = ((item.get("published-online") or item.get("published-print") or {}).get("date-parts") or [[]])[0]
        published = "-".join(str(x).zfill(2) for x in date_parts) if date_parts else ""
        authors = [" ".join([a.get("given", ""), a.get("family", "")]).strip() for a in item.get("author") or []]
        title = (item.get("title") or ["Untitled"])[0]
        journal = (item.get("container-title") or ["Crossref"])[0]
        doi = item.get("DOI") or ""
        papers.append({
            "title": title,
            "abstract": strip_tags(item.get("abstract") or ""),
            "authors": [x for x in authors if x],
            "institutions": [],
            "published": published,
            "journal": journal,
            "doi": doi,
            "url": item.get("URL") or (("https://doi.org/" + doi) if doi else ""),
            "sources": ["Crossref"]
        })
    return papers


def search_arxiv(query, since, limit, config):
    params = {
        "search_query": "all:" + ' AND all:'.join('"%s"' % part for part in query.split()),
        "start": 0,
        "max_results": min(limit, 50),
        "sortBy": "submittedDate",
        "sortOrder": "descending"
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    root = http_get_xml(url, config["search"]["timeout_seconds"])
    atom = {"a": "http://www.w3.org/2005/Atom"}
    cutoff = parse_date(since)
    papers = []
    for entry in root.findall("a:entry", atom):
        published = (entry.findtext("a:published", default="", namespaces=atom) or "")[:10]
        if parse_date(published) < cutoff:
            continue
        entry_url = entry.findtext("a:id", default="", namespaces=atom)
        arxiv_id = entry_url.rsplit("/", 1)[-1]
        papers.append({
            "title": strip_tags(entry.findtext("a:title", default="", namespaces=atom)),
            "abstract": strip_tags(entry.findtext("a:summary", default="", namespaces=atom)),
            "authors": [a.findtext("a:name", default="", namespaces=atom) for a in entry.findall("a:author", atom)],
            "institutions": [],
            "published": published,
            "journal": "arXiv",
            "doi": entry.findtext("{http://arxiv.org/schemas/atom}doi", default=""),
            "arxiv_id": arxiv_id,
            "url": entry_url,
            "sources": ["arXiv"]
        })
    return papers


def merge_papers(papers):
    merged = {}
    title_index = {}
    for paper in papers:
        key = stable_id(paper)
        title_key = normalized_title(paper.get("title"))
        existing_key = title_index.get(title_key) if title_key else None
        if existing_key:
            key = existing_key
        if key not in merged:
            merged[key] = paper
            if title_key:
                title_index[title_key] = key
            continue
        old = merged[key]
        for field in ("abstract", "authors", "institutions", "journal", "doi", "arxiv_id", "openalex_id", "url"):
            if not old.get(field) or (field == "abstract" and len(paper.get(field, "")) > len(old.get(field, ""))):
                old[field] = paper.get(field, old.get(field))
        old["sources"] = sorted(set(old.get("sources", []) + paper.get("sources", [])))
    return list(merged.values())


def keyword_hits(text, terms):
    # Token normalization avoids false positives such as COF in "cofactor" and
    # treats hyphen/en-dash/space variants as equivalent.
    normalized_text = " " + re.sub(r"[^\wπ]+", " ", text.lower(), flags=re.UNICODE).strip() + " "
    matched = []
    for term in terms:
        normalized_term = re.sub(r"[^\wπ]+", " ", term.lower(), flags=re.UNICODE).strip()
        if normalized_term and (" " + normalized_term + " ") in normalized_text:
            matched.append(term)
    return sorted(set(matched))


def score_paper(paper, config):
    paper["title"] = strip_tags(paper.get("title", ""))
    text = " ".join([paper.get("title", ""), paper.get("abstract", "")])
    keyword_groups = config["keywords"]
    hits = {name: keyword_hits(text, terms) for name, terms in keyword_groups.items()}
    excluded = keyword_hits(text, config.get("exclude_phrases", []))
    framework = bool(hits.get("framework"))
    topical_groups = sum(bool(hits.get(name)) for name in ("conductive", "mesoporous", "assembly", "electrochemical"))

    topic = min(35, (12 if framework else 0) + topical_groups * 6 + (5 if framework and topical_groups >= 2 else 0))
    method = min(25, len(hits.get("mesoporous", [])) * 4 + len(hits.get("assembly", [])) * 2 + len(hits.get("electrochemical", [])) * 5)
    novelty = min(15, 4 + topical_groups * 2 + (3 if topical_groups >= 3 else 0))
    network_text = " ".join(paper.get("authors", []) + paper.get("institutions", [])).lower()
    network_hits = [x for x in config.get("tracked_people_and_orgs", []) if x.lower() in network_text]
    network = min(10, len(network_hits) * 5)
    applied = min(10, (4 if hits.get("electrochemical") else 0) + (3 if hits.get("mesoporous") else 0) + (3 if hits.get("assembly") else 0))
    archive = min(5, 1 + topical_groups)
    penalty = min(25, len(excluded) * 15)
    total = max(0, topic + method + novelty + network + applied + archive - penalty)

    paper["score"] = total
    paper["scores"] = {"topic": topic, "method": method, "novelty": novelty, "network": network, "applied": applied, "archive": archive, "penalty": penalty}
    paper["hits"] = hits
    paper["network_hits"] = network_hits
    paper["excluded_hits"] = excluded
    paper["reading_depth"] = "摘要" if paper.get("abstract") else "仅元数据"
    paper["recommendation"] = recommendation(paper)
    return paper


def recommendation(paper):
    labels = []
    translations = {"conductive": "导电/共轭框架", "mesoporous": "介孔化", "assembly": "组装与结晶", "electrochemical": "电化学合成"}
    for key in ("mesoporous", "electrochemical", "conductive", "assembly"):
        if paper.get("hits", {}).get(key):
            labels.append(translations[key])
    if paper.get("network_hits"):
        labels.append("关注作者/机构")
    if labels:
        return "同时命中%s，建议优先检查其合成路径与孔结构证据。" % "、".join(labels)
    return "与晶态多孔框架主题相关，建议根据摘要判断是否进一步阅读。"


def rfc2822(date_value):
    parsed = parse_date(date_value)
    stamp = calendar.timegm(parsed.utctimetuple())
    return email.utils.formatdate(stamp, usegmt=True)


def make_description(paper):
    authors = ", ".join(paper.get("authors", [])[:8]) or "作者信息暂缺"
    abstract = paper.get("abstract") or "当前来源未提供摘要。"
    if len(abstract) > 1200:
        abstract = abstract[:1197] + "..."
    hit_terms = []
    for values in paper.get("hits", {}).values():
        hit_terms.extend(values)
    parts = [
        "<p><strong>相关性评分：</strong>%s/100；<strong>阅读深度：</strong>%s</p>" % (paper.get("score", 0), html.escape(paper.get("reading_depth", ""))),
        "<p><strong>推荐理由：</strong>%s</p>" % html.escape(paper.get("recommendation", "")),
        "<p><strong>作者：</strong>%s</p>" % html.escape(authors),
        "<p><strong>命中词：</strong>%s</p>" % html.escape(", ".join(sorted(set(hit_terms))) or "无"),
        "<p><strong>摘要：</strong>%s</p>" % html.escape(abstract),
        "<p><strong>来源：</strong>%s</p>" % html.escape(", ".join(paper.get("sources", [])))
    ]
    return "".join(parts)


def build_feed(papers, config, generated_at):
    feed_cfg = config["feed"]
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    for tag, value in (("title", feed_cfg["title"]), ("link", feed_cfg["link"]), ("description", feed_cfg["description"]), ("language", feed_cfg.get("language", "zh-CN"))):
        ET.SubElement(channel, tag).text = value
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(generated_at.replace(tzinfo=dt.timezone.utc), usegmt=True)
    ET.SubElement(channel, "generator").text = "Mesoporous Framework Literature RSS"
    ET.SubElement(channel, "{http://www.w3.org/2005/Atom}link", {"href": feed_cfg["link"], "rel": "self", "type": "application/rss+xml"})
    for paper in papers:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = "[%s] %s" % (paper.get("score", 0), paper.get("title", "Untitled"))
        ET.SubElement(item, "link").text = paper.get("url") or feed_cfg["link"]
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = stable_id(paper)
        ET.SubElement(item, "pubDate").text = rfc2822(paper.get("published"))
        ET.SubElement(item, "description").text = make_description(paper)
        ET.SubElement(item, "category").text = paper.get("journal") or "Literature"
    try:
        ET.indent(rss, space="  ")
    except AttributeError:
        pass
    # Python 3.7's ElementTree.tostring has no xml_declaration argument.
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(rss, encoding="utf-8")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write(path, content, binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    mode = "wb" if binary else "w"
    kwargs = {} if binary else {"encoding": "utf-8"}
    with open(tmp, mode, **kwargs) as handle:
        handle.write(content)
    os.replace(tmp, path)


def run(config_path, output_dir, fixture_path=None):
    config = load_json(config_path, {})
    now = utcnow()
    state_path = os.path.join(output_dir, "papers.json")
    is_bootstrap = not os.path.exists(state_path)
    lookback_days = config["search"].get("bootstrap_lookback_days", config["search"]["lookback_days"]) if is_bootstrap else config["search"]["lookback_days"]
    since = (now - dt.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    errors = []
    all_papers = []
    if fixture_path:
        all_papers = load_json(fixture_path, [])
    else:
        source_functions = {"openalex": search_openalex, "crossref": search_crossref, "arxiv": search_arxiv}
        per_query = max(8, config["search"]["candidate_pool_size"] // max(1, len(config["queries"])))
        for query in config["queries"]:
            for source in config["search"]["sources"]:
                try:
                    found = source_functions[source](query, since, per_query, config)
                    all_papers.extend(found)
                    print("%-10s | %2d | %s" % (source, len(found), query))
                except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError, ValueError, KeyError) as exc:
                    message = "%s / %s: %s" % (source, query, exc)
                    errors.append(message)
                    print("WARNING " + message, file=sys.stderr)
                time.sleep(0.15)

    merged = merge_papers(all_papers)
    scored = [score_paper(p, config) for p in merged]
    scored.sort(key=lambda p: (p["score"], parse_date(p.get("published"))), reverse=True)
    # Keep a transparent audit trail, including rejected candidates, so keyword
    # and score calibration does not become a black box.
    review_path = os.path.join(output_dir, "candidate-review.json")
    atomic_write(review_path, json.dumps(scored[:50], ensure_ascii=False, indent=2))
    eligible = [p for p in scored if p["hits"].get("framework") and p["scores"]["topic"] >= 10 and p["score"] >= config["search"]["minimum_score"]]
    eligible.sort(key=lambda p: (p["score"], parse_date(p.get("published"))), reverse=True)
    selected = eligible[:config["search"]["final_selection_count"]]

    previous = load_json(state_path, [])
    combined = merge_papers(selected + previous)
    for paper in combined:
        score_paper(paper, config)
    future_cutoff = now + dt.timedelta(days=2)
    combined = [
        p for p in combined
        if p["hits"].get("framework")
        and p["scores"]["topic"] >= 10
        and p["score"] >= config["search"]["minimum_score"]
        and parse_date(p.get("published")) <= future_cutoff
    ]
    combined.sort(key=lambda p: (parse_date(p.get("published")), p.get("score", 0)), reverse=True)
    combined = combined[:config["feed"].get("max_items", 100)]

    atomic_write(state_path, json.dumps(combined, ensure_ascii=False, indent=2))
    atomic_write(os.path.join(output_dir, "feed.xml"), build_feed(combined, config, now), binary=True)
    report = {"generated_at": now.isoformat() + "Z", "bootstrap": is_bootstrap, "since": since, "raw_candidates": len(all_papers), "unique_candidates": len(merged), "eligible": len(eligible), "selected": len(selected), "feed_items": len(combined), "errors": errors}
    atomic_write(os.path.join(output_dir, "last-run.json"), json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if combined or all_papers else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.json"))
    parser.add_argument("--output", default=os.path.join(os.path.dirname(__file__), "public"))
    parser.add_argument("--fixture", help="Read candidate papers from JSON instead of network APIs")
    args = parser.parse_args()
    return run(os.path.abspath(args.config), os.path.abspath(args.output), args.fixture)


if __name__ == "__main__":
    sys.exit(main())
