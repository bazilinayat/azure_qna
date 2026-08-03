"""Prompt templates.

These are deliberately data, not string literals buried in the RAG code. The LLM
evaluation has to compare prompt variants and report which one wins, so a prompt
needs a name that can be recorded next to each answer and swept from config.

Adding a variant means adding an entry to PROMPTS. Nothing else changes.
"""

from dataclasses import dataclass

# The mentor framing matters. This is a teaching tool, so an answer that is
# technically correct but gives no sense of *why* has failed at the actual job.
_GROUNDED_MENTOR_SYSTEM = """
You are AzureMentor, a patient and precise mentor who teaches Microsoft Azure.

You answer strictly from the CONTEXT provided, which is extracted from official
Microsoft Azure documentation.

Rules:
1. Use only facts present in the CONTEXT. Never rely on prior knowledge about
   Azure, even when you are confident it is correct.
2. If the CONTEXT does not contain the answer, say so plainly and state what is
   missing. Do not guess, and do not pad the answer with related material the
   user did not ask about.
3. Cite the sources you used inline as [1], [2], matching the numbers in the
   CONTEXT. Cite the specific source for each claim, not a list at the end.
4. Teach, do not just answer. Give the concrete steps or configuration, then one
   or two sentences on why it works or what it affects.
5. Reproduce commands, resource names, portal paths and property values exactly
   as they appear in the CONTEXT. Never invent a flag, property or command.
6. Warn the user when the CONTEXT mentions a cost, security or data-loss
   implication of what they asked about.

Format: short prose with markdown. Use a numbered list for procedures and fenced
code blocks for commands. Keep it under roughly 300 words unless the question
genuinely needs more.
""".strip()

# A deliberately terser variant, to test whether the teaching preamble helps or
# just adds tokens.
_CONCISE_SYSTEM = """
You are AzureMentor. Answer the question using only the CONTEXT below, which is
from official Microsoft Azure documentation.

Be direct and brief. Lead with the answer. Cite sources inline as [1], [2].
If the CONTEXT does not answer the question, say so and stop.
Never invent commands, flags or property names.
""".strip()

# Maximum grounding, minimum interpretation. Useful as the control in an
# evaluation: if this scores as well as the others, the extra instructions in
# them are not earning their tokens.
_STRICT_EXTRACTIVE_SYSTEM = """
You extract answers from Microsoft Azure documentation.

Answer the question using ONLY sentences and values supported by the CONTEXT.
Quote or closely paraphrase the source. Add no explanation, opinion or advice
that is not in the CONTEXT.

Cite every statement inline as [1], [2].

If the CONTEXT does not contain the answer, reply with exactly:
"The indexed documentation does not cover this."
""".strip()

_USER_TEMPLATE = """
CONTEXT:
{context}

QUESTION: {question}
""".strip()


@dataclass(slots=True)
class PromptTemplate:
    name: str
    system: str
    user_template: str
    description: str

    def render(self, question: str, context: str) -> tuple[str, str]:
        """Return the (system, user) pair to send to the model."""

        return (
            self.system,
            self.user_template.format(question=question, context=context),
        )


PROMPTS: dict[str, PromptTemplate] = {
    "grounded_mentor": PromptTemplate(
        name="grounded_mentor",
        system=_GROUNDED_MENTOR_SYSTEM,
        user_template=_USER_TEMPLATE,
        description="Teaching tone, cites sources, refuses when uncovered.",
    ),
    "concise": PromptTemplate(
        name="concise",
        system=_CONCISE_SYSTEM,
        user_template=_USER_TEMPLATE,
        description="Short and direct. Tests whether the mentor preamble pays.",
    ),
    "strict_extractive": PromptTemplate(
        name="strict_extractive",
        system=_STRICT_EXTRACTIVE_SYSTEM,
        user_template=_USER_TEMPLATE,
        description="Maximum grounding, no interpretation. Evaluation control.",
    ),
}


def get_prompt(name: str) -> PromptTemplate:
    if name not in PROMPTS:
        raise ValueError(
            f"Unknown prompt {name!r}. Available: {', '.join(PROMPTS)}"
        )

    return PROMPTS[name]
