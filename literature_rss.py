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


def http_post_json(url, payload, headers, timeout):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    request_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
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


def search_openalex(query, date_from, date_to, limit, config):
    params = {
        "search": query,
        "filter": "from_publication_date:%s,to_publication_date:%s" % (date_from, date_to),
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


def search_crossref(query, date_from, date_to, limit, config):
    params = {
        "query.title": query,
        "filter": "from-pub-date:%s,until-pub-date:%s" % (date_from, date_to),
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


def search_arxiv(query, date_from, date_to, limit, config):
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
    cutoff = parse_date(date_from)
    upper = parse_date(date_to)
    papers = []
    for entry in root.findall("a:entry", atom):
        published = (entry.findtext("a:published", default="", namespaces=atom) or "")[:10]
        if parse_date(published) < cutoff or parse_date(published) > upper:
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


def source_quality_score(journal, config):
    normalized = re.sub(r"\s+", " ", html.unescape(strip_tags(journal or "")).lower()).strip()

    def matches(name):
        candidate = re.sub(r"\s+", " ", html.unescape(name).lower()).strip()
        return normalized == candidate or normalized.startswith(candidate + ":")

    premium = [name for name in config.get("quality_journals", {}).get("premium", []) if matches(name)]
    strong = [name for name in config.get("quality_journals", {}).get("strong", []) if matches(name)]
    if premium:
        return 15, "premium", premium
    if strong:
        return 8, "strong", strong
    return 0, "standard", []


def score_paper(paper, config):
    paper["title"] = strip_tags(paper.get("title", ""))
    text = " ".join([paper.get("title", ""), paper.get("abstract", "")])
    keyword_groups = config["keywords"]
    hits = {name: keyword_hits(text, terms) for name, terms in keyword_groups.items()}
    excluded = keyword_hits(text, config.get("exclude_phrases", []))
    framework = bool(hits.get("framework"))
    topical_groups = sum(bool(hits.get(name)) for name in ("conductive", "mesoporous", "assembly", "electrochemical"))

    topic = min(35, (12 if framework else 0) + topical_groups * 6 + (5 if framework and topical_groups >= 2 else 0))
    method = min(20, len(hits.get("mesoporous", [])) * 4 + len(hits.get("assembly", [])) * 2 + len(hits.get("electrochemical", [])) * 5)
    novelty = min(10, 4 + topical_groups * 2 + (2 if topical_groups >= 3 else 0))
    quality, quality_tier, quality_hits = source_quality_score(paper.get("journal", ""), config)
    network_text = " ".join(paper.get("authors", []) + paper.get("institutions", [])).lower()
    network_hits = [x for x in config.get("tracked_people_and_orgs", []) if x.lower() in network_text]
    network = min(10, len(network_hits) * 5)
    applied = min(5, (3 if hits.get("electrochemical") else 0) + (2 if hits.get("mesoporous") else 0) + (2 if hits.get("assembly") else 0))
    archive = min(5, 1 + topical_groups)
    penalty = min(25, len(excluded) * 15)
    total = max(0, min(100, topic + method + quality + novelty + network + applied + archive - penalty))

    paper["score"] = total
    paper["scores"] = {"topic": topic, "method": method, "quality": quality, "novelty": novelty, "network": network, "applied": applied, "archive": archive, "penalty": penalty}
    paper["quality_tier"] = quality_tier
    paper["quality_hits"] = quality_hits
    paper["hits"] = hits
    paper["network_hits"] = network_hits
    paper["excluded_hits"] = excluded
    paper["reading_depth"] = "摘要" if paper.get("abstract") else "仅元数据"
    paper["recommendation"] = recommendation(paper)
    return paper


def classify_paper(paper, config):
    selection = config.get("selection", {})
    framework = bool(paper.get("hits", {}).get("framework"))
    mesoporous = bool(paper.get("hits", {}).get("mesoporous"))
    assembly = bool(paper.get("hits", {}).get("assembly"))
    electrochemical = bool(paper.get("hits", {}).get("electrochemical"))
    conductive = bool(paper.get("hits", {}).get("conductive"))
    materials = bool(paper.get("hits", {}).get("materials"))
    topical = conductive or mesoporous or assembly or electrochemical
    score = paper.get("score", 0)
    scores = paper.get("scores", {})

    if framework and topical and score >= selection.get("core_min_score", config["search"].get("minimum_score", 34)):
        return "核心推荐"

    adjacent_topic = framework or (mesoporous and materials) or (assembly and materials)
    quality_signal = (
        scores.get("quality", 0) >= selection.get("adjacent_quality_min", 8)
        or scores.get("method", 0) >= selection.get("adjacent_method_min", 8)
        or scores.get("network", 0) > 0
    )
    if adjacent_topic and quality_signal and score >= selection.get("adjacent_min_score", 24):
        return "拓展推荐"
    return ""


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


def fallback_chinese_content(paper):
    hit_labels = []
    labels = {"conductive": "导电与共轭特性", "mesoporous": "介孔或分级孔结构", "assembly": "组装与结晶过程", "electrochemical": "电化学合成"}
    for key, label in labels.items():
        if paper.get("hits", {}).get(key):
            hit_labels.append(label)
    focus = "、".join(hit_labels) if hit_labels else "晶态多孔框架材料"
    abstract = strip_tags(paper.get("abstract", ""))
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", abstract) if part.strip()]
    summary_source = " ".join(sentences[:2])[:700]
    method_terms = re.compile(r"\b(method|synthesi[sz]|fabricat|assembl|prepar|characteri[sz]|measure|microscop|spectroscop|electrochem|diffraction|simulation)\w*\b", re.I)
    result_terms = re.compile(r"\b(result|show|demonstrat|reveal|find|found|achiev|exhibit|increase|decrease|enhanc|enable|performance|mechanism)\w*\b|\d", re.I)
    method_sentences = [sentence for sentence in sentences if method_terms.search(sentence)][:2]
    result_sentences = [sentence for sentence in sentences if result_terms.search(sentence)][:3]
    if abstract:
        research_details = "模型服务暂不可用；以下保留原始英文摘要中的核心句，避免用统一套话代替证据：%s" % (summary_source or abstract[:700])
        findings = result_sentences or sentences[-2:] or ["摘要存在，但自动提取未识别出独立结果句。"]
        methods = "模型服务暂不可用；摘要中的方法相关原句：%s" % (" ".join(method_sentences) if method_sentences else "摘要未明确分列方法，需查看原文。")
        summary = "模型服务暂不可用；原始摘要核心内容：%s" % (summary_source[:350] or abstract[:350])
        evidence_note = "摘要级自动摘录（非模型概括）；英文原句来自公开摘要，关键细节仍需全文核查。"
    else:
        evidence_limit = "自动化来源未提供摘要，具体材料体系、实验条件、定量数据和性能结论需查阅全文核实。"
        summary = "该记录仅有题目和元数据，当前无法形成有证据支持的内容概括。"
        research_details = evidence_limit
        findings = [evidence_limit]
        methods = "摘要缺失，无法从现有来源提取方法。"
        evidence_note = "仅元数据级记录；未使用题目之外的信息推断研究结论。"
    return {
        "title_zh": paper.get("title", ""),
        "summary_zh": summary,
        "research_details_zh": research_details,
        "key_findings_zh": findings,
        "methods_zh": methods,
        "relevance_zh": paper.get("recommendation", "与本课题方向相关。"),
        "evidence_note_zh": evidence_note
    }


def parse_model_json(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I | re.S)
    start = content.find("[")
    end = content.rfind("]")
    if start < 0 or end < start:
        raise ValueError("Model response did not contain a JSON array")
    return json.loads(content[start:end + 1])


def model_response_text(response):
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = ""
    if not content:
        raise ValueError("Model response did not contain assistant text")
    return content


def enrich_chinese(papers, config, errors):
    if not papers:
        return papers
    ai_config = config.get("ai", {})
    key_env = ai_config.get("api_key_env", "DEEPSEEK_API_KEY")
    token = os.environ.get(key_env, "").strip()
    if not ai_config.get("enabled", True) or not token:
        for paper in papers:
            paper.update(fallback_chinese_content(paper))
        if ai_config.get("enabled", True):
            errors.append("%s unavailable; used abstract-extractive fallback" % key_env)
        return papers

    batch_size = max(1, int(ai_config.get("batch_size", 5)))
    max_chars = int(ai_config.get("max_abstract_chars", 3500))
    for offset in range(0, len(papers), batch_size):
        batch = papers[offset:offset + batch_size]
        records = [{
            "doi": paper.get("doi", ""),
            "title": paper.get("title", ""),
            "abstract": (paper.get("abstract") or "")[:max_chars],
            "journal": paper.get("journal", ""),
            "relevance_score": paper.get("score", 0),
            "recommendation_tier": paper.get("selection_tier", ""),
            "matched_topics": {key: values for key, values in paper.get("hits", {}).items() if values}
        } for paper in batch]
        prompt = (
            "你是严谨的材料化学文献编辑。关注介孔导电MOF、共轭MOF/COF/HOF、其他框架材料、介孔材料、"
            "自组装技术以及框架材料的组装和电化学合成。仅依据提供的题目和摘要解读，不得利用常识补造该论文"
            "没有明确报告的实验条件、表征、数值、机制或结论；缺失信息必须写“摘要未报告”。"
            "返回严格JSON数组，每项包含doi、title_zh、summary_zh、research_details_zh、key_findings_zh、"
            "methods_zh、relevance_zh、evidence_note_zh。summary_zh用60至100字给出可快速浏览的核心贡献；"
            "research_details_zh用180至320字说明研究问题、材料体系、设计策略、研究过程及意义；"
            "key_findings_zh必须是2至4项字符串数组，优先保留摘要明确报告的定量结果、结构—性能关系、机制证据"
            "和边界条件，没有数值时写明确的定性结论，信息不足时写“摘要未报告相应数据”；"
            "methods_zh用100至200字概述摘要明确出现的合成、组装、表征和性能测试方法；"
            "relevance_zh用80至160字说明对研究主线的实际价值、局限及是否值得全文精读；"
            "evidence_note_zh固定说明这是摘要级解读，哪些关键细节仍需全文核查。所有内容使用中文且不要使用Markdown。输入："
            + json.dumps(records, ensure_ascii=False)
        )
        payload = {
            "model": ai_config.get("model", "deepseek-v4-flash"),
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": "disabled"},
            "temperature": 0.2,
            "max_tokens": int(ai_config.get("max_output_tokens", 3500)),
            "stream": False
        }
        try:
            response = http_post_json(
                ai_config.get("api_url", "https://api.deepseek.com/chat/completions"),
                payload,
                {"Authorization": "Bearer " + token, "Accept": "application/json"},
                config["search"]["timeout_seconds"]
            )
            items = parse_model_json(model_response_text(response))
            by_doi = {str(item.get("doi", "")).lower(): item for item in items}
            for paper in batch:
                generated = by_doi.get(paper.get("doi", "").lower())
                if generated:
                    for key in ("title_zh", "summary_zh", "research_details_zh", "methods_zh", "relevance_zh", "evidence_note_zh"):
                        paper[key] = str(generated.get(key, "")).strip()
                    findings = generated.get("key_findings_zh", [])
                    paper["key_findings_zh"] = [str(item).strip() for item in findings if str(item).strip()] if isinstance(findings, list) else []
                if not paper.get("summary_zh") or not paper.get("research_details_zh"):
                    paper.update(fallback_chinese_content(paper))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
            errors.append("DeepSeek Chinese introduction failed: %s" % exc)
            for paper in batch:
                paper.update(fallback_chinese_content(paper))
    return papers


def make_daily_description(digest):
    papers = digest.get("papers", [])
    parts = [
        "<h2>%s 文献日报</h2>" % html.escape(digest["date"]),
        "<p>检索范围：北京时间昨日 00:00–23:59；经多源去重和相关性筛选，共收录 <strong>%d</strong> 篇带 DOI 的论文。</p>" % len(papers)
    ]
    if not papers:
        parts.append("<p>昨日没有发现达到当前相关性门槛且具有 DOI 的新论文。本日报不以低相关结果凑数。</p>")
        return "".join(parts)
    for index, paper in enumerate(papers, 1):
        authors = ", ".join(paper.get("authors", [])[:10]) or "作者信息暂缺"
        doi = paper.get("doi", "")
        doi_url = "https://doi.org/" + doi
        summary = paper.get("summary_zh") or paper.get("introduction_zh", "")
        research_details = paper.get("research_details_zh") or paper.get("introduction_zh", "")
        findings = paper.get("key_findings_zh") or []
        findings_html = "".join("<li>%s</li>" % html.escape(str(item)) for item in findings)
        parts.extend([
            "<hr><h3>#%d %s</h3>" % (index, html.escape(paper.get("title_zh") or paper.get("title", ""))),
            "<p><strong>原始题目：</strong>%s</p>" % html.escape(paper.get("title", "")),
            "<p><strong>作者：</strong>%s</p>" % html.escape(authors),
            "<p><strong>期刊与日期：</strong>%s；%s</p>" % (html.escape(paper.get("journal", "")), html.escape(paper.get("published", ""))),
            "<p><strong>DOI：</strong><a href=\"%s\">%s</a></p>" % (html.escape(doi_url), html.escape(doi)),
            "<p><strong>推荐层级：</strong>%s；<strong>期刊质量信号：</strong>%s</p>" % (html.escape(paper.get("selection_tier", "")), html.escape(paper.get("quality_tier", "standard"))),
            "<p><strong>精简速览：</strong>%s</p>" % html.escape(summary),
            "<p><strong>重要研究内容：</strong>%s</p>" % html.escape(research_details),
            "<p><strong>关键发现：</strong></p><ul>%s</ul>" % findings_html if findings_html else "<p><strong>关键发现：</strong>当前记录未提供结构化关键发现，请结合原文核查。</p>",
            "<p><strong>方法概述：</strong>%s</p>" % html.escape(paper.get("methods_zh", "")),
            "<p><strong>与你研究方向的关系：</strong>%s</p>" % html.escape(paper.get("relevance_zh", "")),
            "<p><strong>证据边界：</strong>%s</p>" % html.escape(paper.get("evidence_note_zh", "摘要级解读；关键细节需查阅全文核实。")),
            "<p><strong>相关性评分：</strong>%s/100；<strong>阅读深度：</strong>%s</p>" % (paper.get("score", 0), html.escape(paper.get("reading_depth", "")))
        ])
    return "".join(parts)


def markdown_escape(value):
    return str(value or "").replace("|", "\\|").strip()


def write_markdown_archive(digest, archive_root):
    year = digest["date"][:4]
    issue_dir = os.path.join(archive_root, year)
    issue_path = os.path.join(issue_dir, digest["date"] + ".md")
    papers = digest.get("papers", [])
    lines = [
        "# %s 介孔导电框架文献日报" % digest["date"],
        "",
        "> 检索范围：北京时间昨日 00:00–23:59；多源检索、去重和相关性筛选；仅收录具有 DOI 的论文。",
        "",
        "本期收录 **%d 篇**论文。" % len(papers),
        ""
    ]
    if not papers:
        lines.extend(["昨日没有发现达到当前相关性门槛且具有 DOI 的新论文。本期不以低相关结果凑数。", ""])
    for index, paper in enumerate(papers, 1):
        doi = paper.get("doi", "")
        authors = ", ".join(paper.get("authors", [])[:20]) or "作者信息暂缺"
        summary = paper.get("summary_zh") or paper.get("introduction_zh", "")
        research_details = paper.get("research_details_zh") or paper.get("introduction_zh", "")
        findings = paper.get("key_findings_zh") or []
        lines.extend([
            "## %d. %s" % (index, markdown_escape(paper.get("title_zh") or paper.get("title"))),
            "",
            "- **原始题目：** %s" % markdown_escape(paper.get("title")),
            "- **作者：** %s" % markdown_escape(authors),
            "- **期刊：** %s" % markdown_escape(paper.get("journal")),
            "- **发表日期：** %s" % markdown_escape(paper.get("published")),
            "- **DOI：** [%s](https://doi.org/%s)" % (markdown_escape(doi), markdown_escape(doi)),
            "- **推荐层级：** %s" % markdown_escape(paper.get("selection_tier")),
            "- **期刊质量信号：** %s" % markdown_escape(paper.get("quality_tier", "standard")),
            "- **相关性评分：** %s/100" % paper.get("score", 0),
            "- **阅读深度：** %s" % markdown_escape(paper.get("reading_depth")),
            "",
            "### 精简速览",
            "",
            markdown_escape(summary),
            "",
            "### 重要研究内容",
            "",
            markdown_escape(research_details),
            "",
            "### 关键发现",
            "",
        ])
        if findings:
            lines.extend(["- %s" % markdown_escape(item) for item in findings])
        else:
            lines.append("- 当前记录未提供结构化关键发现，请结合原文核查。")
        lines.extend([
            "",
            "### 方法概述",
            "",
            markdown_escape(paper.get("methods_zh")),
            "",
            "### 与研究方向的关系",
            "",
            markdown_escape(paper.get("relevance_zh")),
            "",
            "### 证据边界",
            "",
            markdown_escape(paper.get("evidence_note_zh", "摘要级解读；关键细节需查阅全文核实。")),
            ""
        ])
    atomic_write(issue_path, "\n".join(lines).rstrip() + "\n")
    return issue_path


def update_archive_index(archive_root):
    entries = []
    if os.path.isdir(archive_root):
        for year in os.listdir(archive_root):
            year_path = os.path.join(archive_root, year)
            if not re.match(r"^\d{4}$", year) or not os.path.isdir(year_path):
                continue
            for filename in os.listdir(year_path):
                match = re.match(r"^(\d{4}-\d{2}-\d{2})\.md$", filename)
                if not match:
                    continue
                issue_date = match.group(1)
                issue_path = os.path.join(year_path, filename)
                try:
                    with open(issue_path, "r", encoding="utf-8") as handle:
                        body = handle.read(500)
                    count_match = re.search(r"本期收录 \*\*(\d+) 篇\*\*", body)
                    count = int(count_match.group(1)) if count_match else 0
                except (OSError, ValueError):
                    count = 0
                entries.append((issue_date, year, filename, count))
    entries.sort(reverse=True)
    lines = [
        "# 介孔导电框架文献日报归档",
        "",
        "每天北京时间 08:30 自动更新。每期保存为独立 Markdown 文件，并与 RSS 同步生成。",
        ""
    ]
    current_year = None
    current_month = None
    for issue_date, year, filename, count in entries:
        month = issue_date[5:7]
        if year != current_year:
            lines.extend(["## %s 年" % year, ""])
            current_year = year
            current_month = None
        if month != current_month:
            lines.extend(["### %s 月" % month, ""])
            current_month = month
        lines.append("- [%s 文献日报（%d篇）](%s/%s)" % (issue_date, count, year, filename))
    if not entries:
        lines.append("归档将在下一次日报运行后自动创建。")
    atomic_write(os.path.join(archive_root, "README.md"), "\n".join(lines).rstrip() + "\n")


def build_feed(digests, config, generated_at):
    feed_cfg = config["feed"]
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    for tag, value in (("title", feed_cfg["title"]), ("link", feed_cfg["link"]), ("description", feed_cfg["description"]), ("language", feed_cfg.get("language", "zh-CN"))):
        ET.SubElement(channel, tag).text = value
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(generated_at.replace(tzinfo=dt.timezone.utc), usegmt=True)
    ET.SubElement(channel, "generator").text = "Mesoporous Framework Literature RSS"
    ET.SubElement(channel, "{http://www.w3.org/2005/Atom}link", {"href": feed_cfg["link"], "rel": "self", "type": "application/rss+xml"})
    for digest in digests:
        item = ET.SubElement(channel, "item")
        count = len(digest.get("papers", []))
        ET.SubElement(item, "title").text = "%s 文献日报｜%d篇" % (digest["date"], count)
        archive_base = config.get("archive", {}).get("github_base_url", "").rstrip("/")
        issue_link = "%s/%s/%s.md" % (archive_base, digest["date"][:4], digest["date"]) if archive_base else feed_cfg["link"]
        ET.SubElement(item, "link").text = issue_link
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = "daily:" + digest["date"]
        ET.SubElement(item, "pubDate").text = rfc2822(digest["date"])
        ET.SubElement(item, "description").text = make_daily_description(digest)
        ET.SubElement(item, "category").text = "每日文献日报"
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


def run(config_path, output_dir, fixture_path=None, target_date=None, archive_root_override=None):
    config = load_json(config_path, {})
    now = utcnow()
    china_tz = dt.timezone(dt.timedelta(hours=8))
    china_now = dt.datetime.now(china_tz)
    target_date = target_date or (china_now.date() - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    state_path = os.path.join(output_dir, "papers.json")
    digests_path = os.path.join(output_dir, "digests.json")
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
                    found = source_functions[source](query, target_date, target_date, per_query, config)
                    all_papers.extend(found)
                    print("%-10s | %2d | %s" % (source, len(found), query))
                except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError, ValueError, KeyError) as exc:
                    message = "%s / %s: %s" % (source, query, exc)
                    errors.append(message)
                    print("WARNING " + message, file=sys.stderr)
                time.sleep(float(config["search"].get("request_delay_seconds", 0.6)))

    merged = merge_papers(all_papers)
    scored = [score_paper(p, config) for p in merged]
    scored.sort(key=lambda p: (p["score"], parse_date(p.get("published"))), reverse=True)
    # Keep a transparent audit trail, including rejected candidates, so keyword
    # and score calibration does not become a black box.
    review_path = os.path.join(output_dir, "candidate-review.json")
    atomic_write(review_path, json.dumps(scored[:50], ensure_ascii=False, indent=2))
    eligible = []
    for paper in scored:
        selection_tier = classify_paper(paper, config)
        if (
            iso_date(paper.get("published")) == target_date
            and selection_tier
            and (paper.get("doi") or not config["search"].get("require_doi", True))
        ):
            paper["selection_tier"] = selection_tier
            eligible.append(paper)
    eligible.sort(key=lambda p: (p["score"], parse_date(p.get("published"))), reverse=True)
    core = [paper for paper in eligible if paper["selection_tier"] == "核心推荐"]
    adjacent = [paper for paper in eligible if paper["selection_tier"] == "拓展推荐"]
    adjacent = adjacent[:int(config.get("selection", {}).get("adjacent_max_count", 3))]
    selected = sorted(core + adjacent, key=lambda p: p["score"], reverse=True)
    daily_max = int(config["search"].get("daily_max_count", 0))
    if daily_max > 0:
        selected = selected[:daily_max]
    enrich_chinese(selected, config, errors)

    previous = load_json(state_path, [])
    combined = merge_papers(selected + previous)
    for paper in combined:
        score_paper(paper, config)
        paper["selection_tier"] = classify_paper(paper, config)
    combined = [p for p in combined if p.get("selection_tier") and (p.get("doi") or not config["search"].get("require_doi", True))]
    combined.sort(key=lambda p: (parse_date(p.get("published")), p.get("score", 0)), reverse=True)
    combined = combined[:500]

    digest = {"date": target_date, "generated_at": now.isoformat() + "Z", "papers": selected}
    digests = [d for d in load_json(digests_path, []) if d.get("date") != target_date]
    digests.append(digest)
    digests.sort(key=lambda d: d.get("date", ""), reverse=True)
    digests = digests[:config["feed"].get("max_items", 100)]

    archive_root = archive_root_override or config.get("archive", {}).get("root", "issues")
    if not archive_root_override and not os.path.isabs(archive_root):
        archive_root = os.path.join(os.path.dirname(config_path), archive_root)
    issue_path = write_markdown_archive(digest, archive_root)
    update_archive_index(archive_root)

    atomic_write(state_path, json.dumps(combined, ensure_ascii=False, indent=2))
    atomic_write(digests_path, json.dumps(digests, ensure_ascii=False, indent=2))
    atomic_write(os.path.join(output_dir, "feed.xml"), build_feed(digests, config, now), binary=True)
    try:
        archive_report_path = os.path.relpath(issue_path, os.path.dirname(config_path)).replace("\\", "/")
    except ValueError:
        archive_report_path = os.path.abspath(issue_path).replace("\\", "/")
    report = {"generated_at": now.isoformat() + "Z", "target_date": target_date, "raw_candidates": len(all_papers), "unique_candidates": len(merged), "eligible": len(eligible), "core_selected": len(core), "adjacent_selected": len(adjacent), "selected": len(selected), "daily_digest_items": len(digests), "archive_file": archive_report_path, "errors": errors}
    atomic_write(os.path.join(output_dir, "last-run.json"), json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.json"))
    parser.add_argument("--output", default=os.path.join(os.path.dirname(__file__), "public"))
    parser.add_argument("--fixture", help="Read candidate papers from JSON instead of network APIs")
    parser.add_argument("--date", help="Digest date in YYYY-MM-DD; defaults to yesterday in Asia/Shanghai")
    parser.add_argument("--archive", help="Override Markdown archive directory")
    args = parser.parse_args()
    archive_override = os.path.abspath(args.archive) if args.archive else None
    return run(os.path.abspath(args.config), os.path.abspath(args.output), args.fixture, args.date, archive_override)


if __name__ == "__main__":
    sys.exit(main())
