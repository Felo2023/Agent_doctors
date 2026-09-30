"""Metadata-only discovery for manually curated open-access solved case reports.

Never turns an article into an evaluation prompt or answer key automatically.
Titles, abstracts and diagnoses stay in scorer-owned storage, not agent input.
"""

import argparse
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from validation.harness import PROJECT, write_atomic


PMCID = re.compile(r"PMC[0-9]{5,10}\Z")
BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
MAX_METADATA_BYTES = 4_000_000


def get(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        if response.status != 200:
            raise ValueError("Europe PMC returned a non-success status")
        data = response.read(MAX_METADATA_BYTES + 1)
        if len(data) > MAX_METADATA_BYTES:
            raise ValueError("article metadata too large; review it manually")
        return data


def metadata(pmcid, *, fetch=get):
    if not PMCID.fullmatch(pmcid):
        raise ValueError("expected a PMCID (PMC plus digits)")
    search = BASE + "search?" + urllib.parse.urlencode({"query": pmcid, "format": "json"})
    matches = json.loads(fetch(search)).get("resultList", {}).get("result", [])
    article = next((item for item in matches if item.get("pmcid") == pmcid), None)
    if not article:
        raise ValueError("PMCID not found in Europe PMC index")
    xml = fetch(BASE + pmcid + "/fullTextXML")
    root = ET.fromstring(xml)
    licenses = []
    for node in root.findall(".//{*}permissions/{*}license/{*}license_ref"):
        if node.text:
            licenses.append(node.text.strip())
    if not licenses:
        for node in root.findall(".//{*}permissions/{*}license/{*}license-p"):
            licenses.extend(url for url in re.findall(r"https?://creativecommons\.org/(?:licenses|publicdomain)/[A-Za-z0-9/._-]+", "".join(node.itertext())))
    accepted = [url for url in licenses if url.startswith(("https://creativecommons.org/licenses/by/",
                                                        "https://creativecommons.org/publicdomain/zero/"))]
    return {"pmcid": pmcid, "pmid": article.get("pmid"), "doi": article.get("doi"),
            "title_private_scorer_only": article.get("title"), "journal": article.get("journalTitle"),
            "authors_private_scorer_only": article.get("authorString"),
            "published": article.get("firstPublicationDate"), "publication_types": article.get("pubType"),
            "is_open_access_indexed": article.get("isOpenAccess") == "Y",
            "license_urls": licenses, "licensed_for_review": bool(accepted),
            "full_text_url": BASE + pmcid + "/fullTextXML",
            "status": "candidate_requires_manual_eligibility_review",
            "manual_checks": ["adult patient and suitable diagnostic cutoff", "diagnosis hidden from title/abstract/filename",
                              "patient details appropriately de-identified", "clinical reference diagnosis adjudicated",
                              "figures/third-party content licensed separately", "model memorization/contamination risk"]}


def save_metadata(pmcids, output, *, fetch=get):
    output = Path(output).resolve()
    if output.is_relative_to(PROJECT):
        raise ValueError("answer-bearing metadata cannot be stored inside the repository")
    if not pmcids or len(pmcids) > 30 or len(set(pmcids)) != len(pmcids):
        raise ValueError("select 1-30 unique PMCID candidates")
    results = [metadata(pmcid, fetch=fetch) for pmcid in pmcids]
    write_atomic(output, {"status": "metadata_candidates_only_not_a_benchmark", "cases": results})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmcid", action="append", required=True, help="Candidate PMC ID; repeat flag")
    parser.add_argument("--output", required=True, help="Scorer-owned path outside the repository")
    args = parser.parse_args()
    cases = save_metadata(args.pmcid, args.output)
    print(json.dumps({"candidates": len(cases), "license_urls_present": sum(bool(c["license_urls"]) for c in cases),
                      "status": "manual_review_required", "output": str(Path(args.output).resolve())}, indent=2))


if __name__ == "__main__":
    main()
