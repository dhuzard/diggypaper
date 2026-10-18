# Discovery and exclusion log (2026-10-18)

Bounded: the two seeds from the task, plus controls. No additional positive candidates were
discovered because Gate 0 was not confirmed on the ALURES side (protocol: expand only if Gate 0 confirms).

| Step | Tool | Query / target | Result |
|---|---|---|---|
| Seed A | PubMed connector `get_full_text_article` | PMC11731260 | text returned; `ALURES #nts-es-414463` located in STAR Methods > Animals |
| Seed A licence | `get_copyright_status` | PMID 39811653 | `license.type=null`, `is_open_access=false` (checked_sources: pubmed). Licence NOT verified -> quotes withheld |
| Seed B | PubMed connector | PMC13304911 | text returned; `NTS-RO-406074 v.1, 8 October 2025` in 4.1 Animals |
| Seed B licence | `get_copyright_status` | PMID 42356464 | `CC BY 4.0`, `is_open_access=true`, source=pmc |
| Preprint of B | PubMed `search_articles` | "Kappa Agonism Reversal Tranq-Dope Overdose rodent model preprint" | 0 hits (PubMed does not index the preprint host). Preprint DOI NOT located; reported as `preprint not-located`, not counted twice |
| Control search | PubMed `search_articles` | `APAFIS[Title/Abstract] OR APAFIS[Text Word]` | 4 PMIDs |
| | `get_copyright_status` | 40634645 (PMC12241532) | licence null -> excluded (licence unverified, not needed) |
| | | 37634579 | all rights reserved, no PMC -> excluded |
| | | 32519619 | no copyright data, no PMC -> excluded |
| | | 30763312 (PMC6392334) | CC BY 4.0 -> **selected as negative control C**: APAFIS number, no NTS id, mice are blood-meal hosts |
| ALURES landing page | HTTPS (urllib via session proxy) | environmental-data.ec.europa.eu/alures/datasets.html | blocked: `Tunnel connection failed: 403 Forbidden` (see `gate0-acquisition.json`) |
| ALURES hosts probed | curl | environmental-data.ec.europa.eu, data.europa.eu, webgate.ec.europa.eu, circabc.europa.eu, ec.europa.eu | all `CONNECT 403` (policy denial) |
| Publisher/PMC hosts probed | curl | www.ncbi.nlm.nih.gov, pmc.ncbi.nlm.nih.gov, eutils.ncbi.nlm.nih.gov, europepmc.org, www.ebi.ac.uk, doi.org, api.crossref.org, zenodo.org, www.biorxiv.org, www.mdpi.com | all `CONNECT 403` |
| WebFetch | WebFetch tool | ALURES datasets page | `getaddrinfo ENOTFOUND` |
| Web search for the IDs | WebSearch | `"NTS-RO-406074" OR "NTS-ES-414463" ALURES non-technical summary` | no relevant result (identifiers not indexed); not used as evidence |

Because the sandbox's egress policy denies every official ALURES and every full-text host, the only
route to article text was the PubMed connector; the only ALURES evidence obtainable is the article-side
identifier. No ALURES row was created, inferred or substituted.
