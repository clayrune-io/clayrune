# Incidents

Failures we hit while building Clayrune, written up in enough detail to be
useful to someone who does not use it.

Every one of these is a class of failure that shows up when you run coding
agents at any scale, not just ours. They share one property, which is why they
are collected here rather than buried in a changelog: **each reported success
while doing nothing.** No exception, no red build, no error in a log. The only
reason any of them surfaced is that somebody checked a thing they had been told
was fine.

Where a check exists that would have caught the failure earlier, it is at the
end of the writeup. Most of them are one command.

| incident | the failure class |
|---|---|
| [Your agent's commits may not be on the branch it told you](agent-commits-wrong-branch.md) | An isolation mechanism that reports success while isolating nothing |
| [A CI job that passed for three months without testing anything](ci-job-that-tested-nothing.md) | A test whose premise silently stopped holding |
| [The tests covered the endpoint, so nobody clicked the button](tests-that-never-clicked.md) | Coverage measured on a surface users never touch |
