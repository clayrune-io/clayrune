"""Structural second check for the learning authority guard (MC-1179 ML3).

`mc.distiller._AUTHORITY_PATTERNS` is a list of phrases. Measured 2026-11-16,
7 of 9 plain paraphrases of "act without asking" matched none of them, so the
guard failed OPEN for every wording not on the list. This module is the second
check `_authority_violation` runs when the phrase list finds nothing. It looks
for the SHAPE of an authority grant rather than one wording of it:

  * an approval-seeking verb (ask, confirm, wait for, check in, seek approval)
    governed by a negation ("stop asking the user to confirm", "no need to
    confirm before", "never wait for a human before"). "Stop AND ask the
    user" is the opposite instruction and is not matched: after stop, quit,
    without, instead of, only the -ing form counts;
  * an approval declared standing, already given, or not needed;
  * autonomy granted outright ("act autonomously", "on your own initiative",
    "without a human in the loop", "as you see fit");
  * a human review or an approval step skipped or switched off, or a guard
    (fence, guardrail, rules file) defeated "when it blocks";
  * the agent widening its own access, permissions, rules or credentials.

Rules for anything added here (CLAUDE.md, "Learning-system safety rails"):
deterministic, no model call, no I/O, and it may only ever make the guard
refuse MORE. The phrase list runs first and is not consulted here, so nothing
in this file can let through a text the list refuses.

One deliberate pass, pinned by tests/test_authority_corpus.py: a match is
skipped when a negation sits in front of it in the same clause ("Never skip
the human review", "ask first rather than acting autonomously"). Those TIGHTEN
a gate; the shipped steward skill is written that way and has to keep
installing.

There is no pass for autonomy "on reversible work" (ruled 2026-11-18). Three
promoted artifacts read "Act autonomously on reversible work", "without
pre-approval", "only confirm on irreversible moves" and "the user can override
standing rules". Each narrows an approval step to a subset or lets a rule be
set aside, which is a grant however reasonable the subset sounds: a person
may type that boundary into a rules file, the learning system may not author
it. The last block of shapes below exists for those three.

It is still a list of shapes. A wording outside all of them passes; the
KNOWN_HOLES list in tests/authority_corpus_data.py holds the ones measured.
"""
import re

# One intervening word. Never crosses sentence punctuation, so a bounded run of
# these cannot join the end of one sentence to the start of the next.
_W = r"(?:[\w'/-]+\s+)"

# Negations that take a bare verb ("never ask", "no need to confirm") ...
_NEG_BARE = (
    r"(?:don'?t|do not|does not|doesn'?t|never|no need to|needn'?t|need not|"
    r"not necessary to|no longer|shouldn'?t|should not|must not)"
    r"(?:\s+(?:ever|even|always|just|have to|need to|bother to|stop to|"
    r"pause to|keep))?"
)
# ... and ones that only negate an -ing form ("stop asking", "without
# checking", "instead of waiting"). "Stop and ask" must not match.
_NEG_ING = (
    r"(?:stop|quit|cease|avoid|skip|without|instead of|rather than|"
    r"refrain from|no need for|no more)"
    r"(?:\s+(?:ever|constantly|repeatedly|first|stopping to|pausing to|"
    r"having to|needing to|bothering to))?"
)


def _negated(*verbs: tuple[str, str]) -> str:
    """`verbs` are (bare form, -ing form) regex pairs."""
    bare = '|'.join(f'(?:{b}|{i})' for b, i in verbs)
    ing = '|'.join(i for _, i in verbs)
    return (r"(?:" + _NEG_BARE + r"\s+(?:" + bare + r")|"
            + _NEG_ING + r"\s+(?:" + ing + r"))")


_DET = (r"(?:(?:a|an|the|any|my|your|their|his|her|explicit|human|user|user'?s|"
        r"operator'?s|owner'?s|anyone'?s|anybody'?s|someone'?s|human'?s|"
        r"further|more|another|prior)\s+)")
_APPROVAL = (r"(?:approval|permission|confirmation|sign-?off|consent|"
             r"authori[sz]ation|go-?ahead|clearance|green[- ]light)")
_PERSON = r"(?:human|user|person|operator|owner|anyone|anybody|someone)"
# What follows "confirm" / "check in" / a person when the approval sense is
# meant, as against "confirm the row count", "check in the generated files",
# "ask anyone to upvote" or "wait for the user to report it".
_TAIL_WORDS = (r"before|first|with|each|every|again|anything|prior|"
               r"on (?:each|every)|after each|between|at (?:each|every)")
_GATE_TAIL = r"(?=\s+(?:" + _TAIL_WORDS + r")\b|\s*[.;,!?)]|\s*$)"
_PERSON_TAIL = (r"(?=\s+(?:" + _TAIL_WORDS + r"|to (?:approve|confirm|respond|"
                r"reply|answer|sign|agree|review|ok(?:ay)?|say|verify|"
                r"authori[sz]e|weigh in|decide))\b|\s*[.;,!?)]|\s*$)")
_SKIP_VERBS = (r"skip|bypass|circumvent|ignor|disabl|overrid|remov|drop|defeat|"
               r"evad|rewrit|replac|work(?:ing)? around|get(?:ting)? around|"
               r"turn(?:ing)? off|switch(?:ing)? off")
_SKIP = r"\b(?:" + _SKIP_VERBS + r")\w*"
# The verbs of stopping for a person, the ways of saying "the part that cannot
# be taken back", and the things an agent is bound by.
_GATED = (r"(?:confirm|ask|check in|surface|escalate|pause|stop|wait|"
          r"(?:seek|request|get|need|require)\s+" + _APPROVAL + r")")
_UNDOABLE = (r"(?:irreversibl\w*|destructive|can(?:no|')t be undone|"
             r"can not be undone|not reversible)")
_RULE = (r"(?:rules?|briefs?|guard-?rails?|gates?|instructions?|constraints?|"
         r"restrictions?|polic(?:y|ies))")

_SHAPES: list[tuple[str, str]] = [
    # ── approval-seeking verb under a negation ──────────────────────────────
    ('no-ask',
     _negated((r"ask", r"asking")) + r"\s+(?:"
     r"(?:(?:the|your|a|any)\s+)?(?:" + _PERSON + r"|me)" + _PERSON_TAIL + r"|"
     r"(?:first|before|permission)\b|"
     r"for\s+" + _DET + r"{0,3}" + _APPROVAL + r"\b|"
     r"(?:whether|if)\s+" + _W + r"{0,6}?(?:allowed|permitted|ok(?:ay)?|"
     r"acceptable|approved|safe to|to proceed|you (?:should|can|may))\b)"),
    ('no-confirm',
     r"(?:"
     + _negated((r"confirm", r"confirming")) + _GATE_TAIL + r"|"
     + _negated((r"check\s+in", r"checking\s+in")) + _GATE_TAIL + r"|"
     + _negated((r"check", r"checking"), (r"double-?check", r"double-?checking"),
                (r"verify", r"verifying"), (r"clear", r"clearing"))
     + r"\s+(?:it\s+|this\s+|that\s+|things\s+)?with\s+" + _DET + r"{0,2}"
     r"(?:" + _PERSON + r"|me)\b|"
     + _negated((r"consult", r"consulting")) + r"\s+" + _DET + r"{0,2}"
     r"(?:" + _PERSON + r"|me)\b|"
     + _negated((r"pause", r"pausing")) + r"\s+for\s+" + _DET + r"{0,3}"
     + _APPROVAL + r"\b)"),
    ('no-wait',
     _negated((r"wait", r"waiting")) + r"\s+(?:for|on)\s+" + _DET + r"{0,3}"
     r"(?:(?:" + _APPROVAL + r"|reply|response|answer|ok(?:ay)?|review|"
     r"instructions?)\b|" + _PERSON + _PERSON_TAIL + r")"),
    ('no-approval-needed',
     _negated((r"seek", r"seeking"), (r"request", r"requesting"),
              (r"get", r"getting"), (r"obtain", r"obtaining"),
              (r"needs?", r"needing"), (r"requires?", r"requiring"),
              (r"await", r"awaiting"))
     + r"\s+" + _DET + r"{0,3}" + _APPROVAL + r"\b"),
    ('nobody-to-consult',
     _PERSON + r"\s+(?:does not|doesn'?t|do not|don'?t|need not|needn'?t|never)"
     r"\s+(?:need|needs|have|has)\s+to\s+(?:be\s+)?(?:consulted|asked|"
     r"approve|confirm|sign off|review|involved|weigh in)"),
    # ── approval declared standing, already given, or not needed ────────────
    ('standing-approval',
     r"(?:standing|blanket|permanent|perpetual|implicit|unconditional|"
     r"unlimited|open-ended|carte blanche)\s+" + _APPROVAL + r"\b"),
    ('pre-approved',
     r"\b(?:has|have|had|is|are|was|were|been|as|consider\w*|treat\w*|deem\w*)"
     r"\s+(?:(?:already|all|now|everything|it|them|this|these)\s+){0,2}"
     r"(?:pre-?approved|approved in advance|pre-?authori[sz]ed)\b"),
    ('approval-unneeded',
     r"\b(?:" + _APPROVAL + r"|(?:human|manual|user|code|peer|plan)\s+review)s?"
     r"\s+(?:(?:gate|step|prompt|check)s?\s+)?"
     r"(?:(?:is|are|as|was|were|to be)\s+)?"
     r"(?:(?:already|now|always|simply|just)\s+)?"
     r"(?:unnecessary|optional|satisfied|implied|assumed|waived|redundant|"
     r"pointless|a waste|not (?:needed|required|necessary)|"
     r"no longer (?:needed|required|necessary)|"
     r"never (?:needed|required|necessary))"),
    ('approval-presumed',
     r"\b(?:consider|assume|treat|presume|regard)\w*\s+" + _W + r"{0,3}?"
     + _APPROVAL + r"\s+(?:(?:gate|step)s?\s+)?(?:as\s+|to be\s+|is\s+|was\s+)?"
     r"(?:already\s+)?(?:granted|given|satisfied|implied|obtained)"),
    ('assume-yes',
     r"\bassum\w+\s+(?:that\s+)?(?:the answer is\s+|it'?s\s+(?:a\s+)?|a\s+)?yes\b"
     r"|\bforgiveness\s+(?:rather than|not|instead of|over)\s+permission"
     r"|\b(?:silence|no (?:reply|response|answer|objection))\s+"
     r"(?:as|is|means|equals|counts as)\s+(?:consent|approval|agreement|"
     r"a yes|permission)"
     r"|\bauto-?(?:accept|confirm|grant|allow)\w*"
     r"|\bautomatically\s+(?:approv|accept|grant|confirm)\w*"),
    ('act-first',
     r"\b(?:act|do it|proceed|ship|push|deploy|merge|delete|send|publish)\s+"
     r"first,?\s+(?:and\s+|then\s+)?(?:tell|inform|notify|report|ask|explain|"
     r"apologi[sz]e)"),
    # ── autonomy granted outright ───────────────────────────────────────────
    # act/operate/work/decide/behave: the agent. "The job runs autonomously
    # on a schedule" describes a job, so run/execute only count with
    # "unsupervised".
    ('autonomy',
     r"\b(?:act|operate|work|decide|behave|pursu)\w*\s+" + _W + r"{0,3}?"
     r"(?:autonomously|unsupervised)\b"
     r"|\b(?:run|execute)\w*\s+unsupervised\b"),
    ('own-authority',
     r"\bon (?:your|its) own (?:authority|initiative|judge?ment)\b"
     r"|\bwithout (?:supervision|oversight)\b"
     r"|\b(?:zero|no|minimal|without|without any)\s+human\s+(?:oversight|"
     r"supervision|involvement|input|intervention|sign-?off|checks?)\b"
     r"|\bproceed\w*\s+unattended\b"
     r"|\b(?:full|complete|total|sole|unlimited)\s+(?:discretion|authority|"
     r"latitude)\b|\bfree (?:rein|hand)\b"),
    ('nobody-watching',
     r"\bwith(?:out)?\s+(?:(?:a|any|the)\s+)?(?:human|user|person|anyone|"
     r"anybody|nobody|no one|no-one)\s+(?:being\s+)?(?:involved|in the loop|"
     r"watching|reviewing|consulted|checking|approving|supervising|looking|"
     r"signing off)"),
    ('as-you-see-fit',
     r"\b(?:as|whenever|wherever|however|whatever(?:\s+\w+)?)\s+"
     r"(?:you|it|the agent)\s+(?:see|think|judge|deem|feel)s?\s+"
     r"(?:fit|best|it helps|appropriate|it best|is best)"),
    ('irreversible-alone',
     r"\b(?:irreversible|destructive)\s+" + _W + r"{0,3}?(?:alone|yourself|"
     r"by yourself|by itself|on your own|independently|unprompted)\b"),
    # ── a review or approval step skipped; a guard defeated ─────────────────
    ('gate-skipped',
     _SKIP + r"\s+(?:(?:the|a|any|all|every|that|this|those|these)\s+)?"
     + _W + r"{0,2}?(?:"
     r"(?:human|manual|user)\s+(?:review|approval|sign-?off|confirmation|"
     r"check)s?|"
     r"(?:[\w-]+-)?(?:approval|permission|confirmation)\s+(?:check|prompt|"
     r"hook|gate|step|stage|guard|dialog|requirement|phase|screen)s?|"
     r"safety\s+(?:hook|rail|guard|interlock)s?|sign-?offs?)\b"),
    # A fence, guardrail or rules file named next to a verb is how this
    # project's own skills describe what NOT to do ("- Working around the
    # fence."), so the noun alone is not enough: the sentence also has to say
    # the guard is in the way.
    ('guard-defeated',
     r"\b(?:" + _SKIP_VERBS + r"|edit|modif|chang|patch|amend|delet|trim)\w*\s+"
     r"(?:(?:the|a|any|that|this|your)\s+)?" + _W + r"{0,2}?"
     r"(?:fence|guard-?rails?|process[- ]guard|hooks?|AGENT_RULES|"
     r"SHARED_RULES)\b(?:\.md|\.py)?[^.;!?]{0,60}?\b(?:"
     r"(?:when|if|whenever|once|because|so)\s+[^.;!?]{0,30}?(?:block|"
     r"in the way|stops? you|refus|slow|filter|restrict|limit)|"
     r"that\s+(?:protect|guard|enforce|constrain|limit|restrict)s?\b)"),
    ('limit-removed',
     _SKIP + r"\s+[^.;!?]{0,60}?\bthat\s+(?:constrain|limit|restrict|bind|"
     r"govern|filter|block)s?\s+(?:you\b|what you|your)"),
    # ── the agent widening its own access, rules or credentials ─────────────
    ('self-grant',
     r"\b(?:give|grant|award|assign|mint|issue|provision)\w*\s+yourself\b"
     r"|\ballow\w*\s+(?:every|all|any)\s+(?:tools?|commands?)\b"),
    ('own-rules',
     r"\b(?:edit|rewrit|modif|chang|updat|alter|patch|amend|loosen|relax|"
     r"trim)\w*\s+(?:your|its)\s+own\s+(?:system prompt|instructions|rules|"
     r"guard-?rails|permissions?|config\w*|settings|allow-?list|constraints|"
     r"limits)"
     r"|\b(?:to|in|into)\s+(?:your|its)\s+own\s+(?:config\w*|settings|"
     r"allow-?list|permissions?|rules|system prompt|instructions|tool ?set)"
     r"|\b(?:edit|rewrit|modif|chang|alter|patch|amend|loosen|relax)\w*\s+"
     r"(?:your\s+(?:system prompt|guard-?rails|permissions?|allow-?list|"
     r"constraints)|the system prompt (?:that\s+)?you (?:run|operate|work) "
     r"under)"
     r"|\b(?:remov|delet|strip|drop)\w*\s+(?:the\s+)?(?:guard-?rails?|"
     r"restrictions?|limits?|safety rules?)\s+from\s+your\b"),
    ('own-privileges',
     r"\b(?:rais|elevat|increas|upgrad|escalat|widen|expand|extend)\w*\s+"
     r"(?:your|its)\s+own\s+" + _W + r"{0,1}?(?:permissions?|privileges?|"
     r"access|authority|scope|trust)"),
    ('skip-permissions',
     r"\b(?:enabl|turn(?:ing)? on|us|pass|set|add)\w*\s+" + _W + r"{0,2}?"
     r"(?:--?dangerously-skip-permissions|skip-?permissions|"
     r"bypass-?permissions)"),
    ('own-credentials',
     r"\b(?:creat|mint|generat|issu|provision)\w*\s+" + _W + r"{0,3}?"
     r"(?:api keys?|credentials?|access tokens?|secrets?|service accounts?)\s+"
     + _W + r"{0,2}?(?:yourself|on your own)\b"),
    # ── an approval step narrowed to a subset; a rule made optional ─────────
    ('without-pre-approval',
     r"\bwithout\s+" + _DET + r"{0,3}pre-?(?:approval|authori[sz]ation|"
     r"clearance|confirmation)\b"),
    ('gate-only-irreversible',
     r"\b(?:only\s+(?:batch and\s+)?" + _GATED + r"|" + _GATED + r"\s+only)\b"
     r"[^.;!?]{0,60}?\b" + _UNDOABLE),
    ('approval-reserved',
     r"\b(?:" + _APPROVAL + r"|approvals?|confirmations?)\s+(?:should be|"
     r"must be|is|are|to be)\s+(?:only\s+)?(?:reserved|saved|kept)\s+for\b"),
    ('no-ask-question',
     _negated((r"ask", r"asking")) + r"\s+(?:(?:the|your)\s+" + _PERSON
     + r"\s+)?[\"'“‘(]?\s*(?:do you want|should i|shall i|would you like|"
     r"may i|can i|is it ok(?:ay)?|want me to)\b"),
    ('no-reconfirm',
     r"(?:" + _NEG_BARE + r"|" + _NEG_ING + r")\b[^.;!?]{0,80}?\b(?:ask(?:ing)?"
     r"\s+for|seek(?:ing)?|request(?:ing)?|wait(?:ing)?\s+for|requir\w+|"
     r"need\w*)\s+" + _DET + r"{0,3}re-?(?:confirmation|approval|"
     r"authori[sz]ation)\b"),
    ('rules-overridable',
     r"\b(?:users?|operators?|owners?|humans?)\s+(?:can|may|should be able to|"
     r"should have the ability to|(?:is|are)\s+(?:able|allowed|free)\s+to)\s+"
     r"(?:(?:explicitly|always|simply|freely)\s+)?(?:overrid|overrul|bypass|"
     r"waiv|suspend|lift|set aside)\w*\s+" + _W + r"{0,4}?" + _RULE + r"\b"),
    ('rules-bypassed',
     r"\b" + _RULE + r"\s+(?:(?:sometimes|occasionally|often|may|might)\s+)?"
     r"(?:need|needs|have|has|ought)\s+to\s+be\s+(?:bypassed|skipped|ignored|"
     r"overridden|set aside|broken|waived)\b"),
    ('override-honoured',
     r"\b(?:hono[u]?r|obey|follow|apply|accept)\w*\s+(?:that|the|any|every|"
     r"such an?|an)\s+override\s+(?:immediately|at once|straight away|"
     r"without\b)"),
]

_COMPILED = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in _SHAPES]

_PARAGRAPH_BREAK = re.compile(r'\n\s*\n|\n(?=\s*(?:[-*+]|\d+[.)])\s)')
_SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+')
_CLAUSE_BREAK = re.compile(r'[;:]|\s[-–—]+\s|[–—]')
_MARKUP = re.compile(r'[*`]|(?<!\w)_+|_+(?!\w)')
_PRIOR_NEGATION = re.compile(
    r"\b(?:never|not|cannot|nor|neither|nobody|without|dont|rather than|"
    r"instead of)\b|n't\b",
    re.IGNORECASE)


def _sentences(text: str):
    """Hard-wrapped lines are one sentence; a blank line or a list item is a
    break. Emphasis marks are dropped so `**ask** the user` reads as words."""
    text = (text or '').replace('\r', '').replace('’', "'")
    for para in _PARAGRAPH_BREAK.split(text):
        para = ' '.join(_MARKUP.sub('', para).split())
        for sentence in _SENTENCE_BREAK.split(para):
            if sentence:
                yield sentence


def _tightens(sentence: str, start: int) -> bool:
    """A negation in front of the match, inside the same clause."""
    clause_start = 0
    for m in _CLAUSE_BREAK.finditer(sentence, 0, start):
        clause_start = m.end()
    return bool(_PRIOR_NEGATION.search(sentence[clause_start:start]))


def paraphrase_violation(text: str) -> str:
    """Return the matched wording if `text` has the shape of an authority
    grant, else ''. Never raises on a string; `None` and '' return ''."""
    for sentence in _sentences(text):
        for _name, rx in _COMPILED:
            for m in rx.finditer(sentence):
                if _tightens(sentence, m.start()):
                    continue
                return m.group(0)
    return ''
