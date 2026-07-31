"""Deterministic query expansion for Azure terminology.

Azure documentation and Azure users rarely use the same words: docs say "Azure
Kubernetes Service", people type "aks". Vector search partly bridges that, but
BM25 cannot match a term that is not present, so acronyms are expanded before
retrieval.

Expansion works by *substitution into the original query*, not by emitting bare
terms. The previous version added the synonym on its own, so "how do I secure a
blob?" also searched for the standalone string "blob storage", discarding the
user's actual question and polluting the fusion with off-topic results.

This is the rule-based baseline. An LLM-backed rewriter belongs on top of it once
the LLM layer exists — and the retrieval evaluation is what should decide whether
it actually beats these rules.
"""

import re

# Written as whole-word patterns so "vm" does not fire inside "vmware".
SYNONYMS: dict[str, list[str]] = {
    "vm": ["virtual machine"],
    "vms": ["virtual machines"],
    "aks": ["Azure Kubernetes Service"],
    "acr": ["Azure Container Registry"],
    "acl": ["access control list"],
    "blob": ["blob storage"],
    "adf": ["Azure Data Factory"],
    "asp": ["App Service plan"],
    "nsg": ["network security group"],
    "vnet": ["virtual network"],
    "rbac": ["role-based access control"],
    "arm": ["Azure Resource Manager"],
    "sas": ["shared access signature"],
    "aad": ["Microsoft Entra ID", "Azure Active Directory"],
    "entra": ["Microsoft Entra ID", "Azure Active Directory"],
    "lb": ["load balancer"],
    "waf": ["web application firewall"],
    "apim": ["API Management"],
    "cdn": ["content delivery network"],
    "mi": ["managed identity"],
    "kv": ["key vault"],
    "afd": ["Azure Front Door"],
    "er": ["ExpressRoute"],
}

MAX_VARIANTS = 4


class QueryExpander:

    def __init__(self, max_variants: int = MAX_VARIANTS) -> None:
        self.max_variants = max_variants

        self._patterns = {
            term: re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
            for term in SYNONYMS
        }

    def expand(self, query: str) -> list[str]:
        """Return the original query plus acronym-substituted variants."""

        variants = [query]

        for term, replacements in SYNONYMS.items():

            pattern = self._patterns[term]

            if not pattern.search(query):
                continue

            for replacement in replacements:

                variant = pattern.sub(replacement, query)

                if variant not in variants:
                    variants.append(variant)

                if len(variants) >= self.max_variants:
                    return variants

        return variants
