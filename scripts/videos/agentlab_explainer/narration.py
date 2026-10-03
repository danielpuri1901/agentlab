"""The narration for the AgentLab explainer video, one entry per beat.

Each beat's text is spoken by Polly and burned in as the caption, so it is
written for the ear and the eye at once: short sentences, one idea each.
SPOKEN_FIXES rewrites only the audio copy for words a voice reads badly.
Every claim here is checked against the code or the ledger (2026-10-03).
"""

CHAPTERS = [
    {
        "file": "ch1_intro.py",
        "number": 1,
        "title": "AgentLab",
        "beats": [
            "This is AgentLab. Every day it reads new AI research, turns the best of it into short video lessons, and learns what its one reader wants.",
            "It runs on AWS: scheduled Fargate tasks, one DynamoDB table, one S3 bucket, and a Lambda. Telegram is the only interface.",
            "Three loops make it work. An experiment loop measures agent techniques with paired statistics.",
            "A lesson loop proposes papers and turns the approved ones into narrated videos.",
            "And a taste loop learns from every tap, and from every silence.",
        ],
    },
    {
        "file": "ch2_experiments.py",
        "number": 2,
        "title": "The experiment loop",
        "beats": [
            "AgentLab started as an experiment lab. A new technique, like a better way to compress an agent's context, has to beat a baseline on the same tasks.",
            "An experiment is one message on an SQS queue. An EventBridge Pipe hands it to Step Functions.",
            "Step Functions runs the baseline arm, then the candidate arm, each as an ARM64 Fargate task on the same tasks.",
            "A finalizer pairs the two logs task by task and runs a paired t-test, with a 95 percent confidence interval on the difference.",
            "If the whole interval is above zero and no protected metric regressed, the verdict is PROMOTE. Entirely below zero is REJECT. Anything else is INCONCLUSIVE.",
            "The first real finding: a summary prompt that keeps exact identifiers first beat a general structured prompt by 27 points of recall on Nova, and 14 on Haiku.",
            "Every state change is an event in the ledger, every log and report lands in S3, and the verdict arrives on Daniel's phone with a chart.",
        ],
    },
    {
        "file": "ch3_proposer.py",
        "number": 3,
        "title": "The proposer",
        "beats": [
            "Every morning at 9:30 Amsterdam time, EventBridge Scheduler starts a small Fargate task: the proposer.",
            "It gathers fresh sources: the newest arXiv papers in four categories, Hugging Face daily papers, and Hacker News links about agents.",
            "GitHub release notes used to be in this pool. The ledger showed 10 of 12 such proposals rejected, so they are gone.",
            "Slot one is filled by code, not by a model: the next foundational paper from a curated list of classics.",
            "For the other slots, Claude reads the sources, Daniel's taste profile, and the last twenty proposals with their outcomes, then picks today's lessons.",
            "Each pick keeps its real title, gets one line of why, and a lens: foundational, frontier, or implement. A pick may only claim what its source states.",
            "At most three a day arrive in Telegram, with approve and reject buttons. Even a capped day sends a message, because silence must mean broken.",
        ],
    },
    {
        "file": "ch4_tap.py",
        "number": 4,
        "title": "One tap",
        "beats": [
            "A tap goes to a Lambda behind a function URL. It checks a secret header first, then checks that the tap came from Daniel's own Telegram account.",
            "The verdict is written with a conditional update. The first tap wins, and a double tap cannot overwrite it.",
            "An approval starts the explain task on Fargate with the cited URL, the real title, and the proposal id, so the video can be traced back to the tap that asked for it.",
        ],
    },
    {
        "file": "ch5_video.py",
        "number": 5,
        "title": "From paper to video",
        "beats": [
            "The explain task runs on four virtual CPUs and turns one source into one lesson.",
            "Deep read. Claude Sonnet reads the page text and writes a full digest plus a strict scene plan. Every number must appear in the source, and every digest has a limits section.",
            "Storyboard. One visual metaphor carries the whole paper, split into eight to ten narrated beats, each grounded in the digest.",
            "Scene code. The model writes animation code against a small base class. A syntax tree guard allows only safe imports and names before anything runs.",
            "Render. Polly narrates each beat. The base class times every beat to its narration, burns in captions, and fails the render if anything leaves the stage.",
            "Judge. Three frames per beat are scored against the storyboard. Seven out of ten passes. Weak substance means a retry. Weak cosmetics still ship.",
            "Every retry is bounded: three storyboards, four scene attempts, and a fifteen minute deadline. A failed paper gets one more chance, never an endless loop.",
            "If no video survives, Daniel gets the digest link and the reason instead. Template filler is never sent.",
            "A finished video carries its model cost and two rows of buttons: cool, meh, or skip for the topic, and clear or unclear for the teaching.",
        ],
    },
    {
        "file": "ch6_flywheel.py",
        "number": 6,
        "title": "The taste flywheel",
        "beats": [
            "The newest loop is the taste flywheel. Its one job: make tomorrow's picks better than today's.",
            "It starts from episodes, derived from the ledger at read time. An approval counts plus one, a rejection minus one. Cool is plus one, meh minus a half, skip minus one.",
            "Silence counts too. A proposal left untouched for 48 hours, or a video left unrated for 72, is a weak negative of minus a half.",
            "Daniel's hand-labelled golden papers join as fixed examples. Episodes older than sixty days count half.",
            "Every Sunday at six in the evening, a consolidation job runs. A hash of each paper's identity puts a fixed thirty percent aside. The model never sees them.",
            "Claude rewrites the taste profile from the rest: what Daniel prefers and avoids, with evidence counts and real examples. Code checks the format and drops any example it cannot find.",
            "Then the gate. For every held-out paper, a probe asks: would Daniel approve this? Once with the old profile, once with the new one.",
            "The new profile ships only if its F1 score drops by no more than two points, and recall on the golden papers does not drop at all.",
            "On the live ledger, the first run moved F1 from 0.20 to 0.25, and golden recall from 0.71 to 0.75.",
            "Each version lives in S3 behind one pointer. Daniel gets a summary of what changed, and one REVERT tap flips the pointer back.",
        ],
    },
    {
        "file": "ch7_trust.py",
        "number": 7,
        "title": "Built to be trusted",
        "beats": [
            "All of it is Terraform. Secrets live in SSM Parameter Store, each task role gets only the actions it uses, and a fifty dollar monthly budget alarm guards spend.",
            "Two container images, a worker and a video renderer. Both builds run all 500 tests, and a single failure stops the build.",
            "One rule runs through every part: silence must mean broken. Every run reports, even when it has nothing new to say.",
            "That is AgentLab. An experiment lab, a lesson factory, and a taste loop, all closing on one person's taps.",
            "Approve what you want to learn. Ignore what you don't. The lab learns the difference.",
        ],
    },
]

SPOKEN_FIXES = {
    "arXiv": "archive",
    "PROMOTE": "promote",
    "REJECT": "reject",
    "INCONCLUSIVE": "inconclusive",
    "REVERT": "revert",
    "9:30": "nine thirty",
    "ARM64": "arm 64",
    "t-test": "t test",
    "0.20": "point two zero",
    "0.25": "point two five",
    "0.71": "point seven one",
    "0.75": "point seven five",
}


def spoken(text: str) -> str:
    for written, said in SPOKEN_FIXES.items():
        text = text.replace(written, said)
    return text
