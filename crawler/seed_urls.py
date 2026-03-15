"""
TASO Crawler — Seed URLs

Curated starting points for each crawler type.
Priority: 1 (low) → 10 (critical).

Sources are public, openly accessible sites used for defensive
security research. No illegal marketplaces are seeded here.
"""
from __future__ import annotations
from typing import List, Tuple

# (url, source_type, priority)
SeedEntry = Tuple[str, str, int]


# ─── Clearnet security & hacking research sites ──────────────────────────────

CLEARNET_SEEDS: List[SeedEntry] = [
    # CVE / Vulnerability databases
    ("https://cve.mitre.org/cgi-bin/cvekey.cgi?keyword=security",   "clearnet", 10),
    ("https://nvd.nist.gov/feeds/json/cve/1.1/nvdcve-1.1-recent.json", "clearnet", 10),
    ("https://www.exploit-db.com/",                                   "clearnet", 9),
    ("https://packetstormsecurity.com/",                              "clearnet", 9),
    ("https://seclists.org/fulldisclosure/",                         "clearnet", 9),

    # Security news
    ("https://thehackernews.com/",                                   "clearnet", 8),
    ("https://krebsonsecurity.com/",                                 "clearnet", 8),
    ("https://www.bleepingcomputer.com/",                            "clearnet", 8),
    ("https://www.darkreading.com/",                                 "clearnet", 8),
    ("https://www.securityweek.com/",                                "clearnet", 8),
    ("https://isc.sans.edu/",                                        "clearnet", 8),
    ("https://www.schneier.com/",                                    "clearnet", 7),
    ("https://threatpost.com/",                                      "clearnet", 7),
    ("https://www.vice.com/en/section/tech/cybersecurity",          "clearnet", 7),
    ("https://arstechnica.com/security/",                           "clearnet", 7),
    ("https://www.wired.com/category/security/",                    "clearnet", 7),

    # Hacking / research forums (clearnet)
    ("https://www.hackforums.net/",                                  "clearnet", 8),
    ("https://0day.today/",                                          "clearnet", 9),
    ("https://vulners.com/",                                         "clearnet", 9),
    ("https://www.rapid7.com/db/",                                   "clearnet", 8),
    ("https://www.zerodayinitiative.com/advisories/published/",     "clearnet", 8),
    ("https://github.com/swisskyrepo/PayloadsAllTheThings",         "clearnet", 7),
    ("https://github.com/danielmiessler/SecLists",                  "clearnet", 7),

    # Threat intelligence / malware
    ("https://otx.alienvault.com/",                                  "clearnet", 8),
    ("https://abuse.ch/",                                            "clearnet", 8),
    ("https://www.virustotal.com/gui/home/search",                  "clearnet", 7),
    ("https://bazaar.abuse.ch/browse/",                             "clearnet", 8),
    ("https://urlhaus.abuse.ch/browse/",                            "clearnet", 8),
    ("https://feodotracker.abuse.ch/browse/",                       "clearnet", 8),
    ("https://www.phishtank.com/",                                   "clearnet", 7),
    ("https://openphish.com/",                                       "clearnet", 7),

    # Paste / leak monitoring
    ("https://pastebin.com/archive",                                 "clearnet", 6),
    ("https://paste.debian.net/",                                    "clearnet", 5),

    # Mailing lists / advisories
    ("https://seclists.org/bugtraq/",                               "clearnet", 8),
    ("https://seclists.org/oss-sec/",                               "clearnet", 8),
    ("https://www.openwall.com/lists/oss-security/",                "clearnet", 8),
    ("https://lists.debian.org/debian-security-announce/",         "clearnet", 7),
    ("https://www.redhat.com/archives/rhsa-announce/",             "clearnet", 7),

    # OSINT & recon
    ("https://www.shodan.io/",                                       "clearnet", 7),
    ("https://censys.io/",                                           "clearnet", 7),
    ("https://www.robtex.com/",                                      "clearnet", 6),

    # Cybercrime research (academic/journalistic)
    ("https://www.recordedfuture.com/blog/",                        "clearnet", 7),
    ("https://www.crowdstrike.com/blog/",                           "clearnet", 7),
    ("https://unit42.paloaltonetworks.com/",                        "clearnet", 7),
    ("https://blog.talosintelligence.com/",                         "clearnet", 7),
    ("https://thedfirreport.com/",                                   "clearnet", 8),
]


# ─── .onion seeds (Tor) ───────────────────────────────────────────────────────
# These are well-documented, publicly listed onion addresses used in
# cybersecurity research. Sources: academic papers, journalism, public indexes.

ONION_SEEDS: List[SeedEntry] = [
    # Tor Project itself
    ("http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/", "onion", 10),

    # Dread — major dark web forum (security, privacy topics)
    ("http://dreadytofatroptsdj6io7l3xptbet6onoyno2yv7jicoxknyazubrad.onion/", "onion", 10),

    # Onion search engines / directories
    ("http://hss3uro2hsxfogfq.onion/",                              "onion", 9),  # Not Evil
    ("http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/", "onion", 9),  # Ahmia
    ("http://3bbaaaccczcbdddz.onion/",                              "onion", 8),
    ("http://torlinksge6enmcyyuxjpjkoouw4oorgdgeo7ftnq3zodj7g2zxi3kyd.onion/", "onion", 9),  # TorLinks

    # Privacy / security focused
    ("http://darkfailenbsdla5mal2mxn2uz66od5vtzd5qozslagrfzachha3f3id.onion/", "onion", 9),  # dark.fail directory

    # SecureDrop onion addresses (whistleblowing, journalism)
    ("http://sdolvtfhatvsysc6l34d65ymdwxcujausv7k5jk4cy5ttzhjoi6fzvyd.onion/", "onion", 7),

    # Onion paste services
    ("http://pastephp3xcfxmb.onion/",                               "onion", 6),

    # I2P eepsite gateway
    ("http://i2pwww.i2p.xyz/",                                      "clearnet", 5),
]


# ─── IRC servers and channels ─────────────────────────────────────────────────

IRC_TARGETS = [
    {
        "network":  "Libera.Chat",
        "host":     "irc.libera.chat",
        "port":     6697,
        "tls":      True,
        "channels": [
            "#security", "#netsec", "#privacy", "#tor",
            "#exploit", "#pentesting", "#malware",
        ],
    },
    {
        "network":  "OFTC",
        "host":     "irc.oftc.net",
        "port":     6697,
        "tls":      True,
        "channels": [
            "#security", "#debian-security", "#tor", "#i2p",
        ],
    },
    {
        "network":  "EFnet",
        "host":     "irc.efnet.org",
        "port":     6667,
        "tls":      False,
        "channels": [
            "#security", "#hacking", "#network",
        ],
    },
]


# ─── Usenet newsgroups ────────────────────────────────────────────────────────

NEWSGROUP_TARGETS = [
    "alt.security",
    "alt.security.pgp",
    "alt.hacking",
    "comp.security.misc",
    "comp.security.announce",
    "comp.security.firewalls",
    "comp.security.unix",
    "sci.crypt",
    "alt.2600",
    "alt.privacy",
    "alt.privacy.anon-server",
    "alt.anonymous",
    "comp.risks",
    "misc.security",
]

# Public NNTP server (free, no auth)
NNTP_SERVER  = "news.eternal-september.org"
NNTP_PORT    = 119
NNTP_MAX_AGE_DAYS = 30   # fetch articles posted within last N days
