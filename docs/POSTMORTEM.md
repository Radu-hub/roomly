# Postmortem

> **Status: not filled in yet.** This must describe a real incident from deploying Roomly, in your own words. Don't submit it until it does: the assignment asks how *you* found a real cause, and reviewers will ask follow-up questions.

## Summary
What broke, in one or two sentences.

## Impact
What stopped working, for whom, and for how long. (For example: "The live site was down during my first deploy; no real users.")

## Timeline
All times UTC.
- `HH:MM` First sign of trouble
- `HH:MM` ...
- `HH:MM` Fixed

## Detection
How did you find out? An alarm, a failed deploy, a browser error?

## Investigation
What did you check, in order? Include the wrong guesses and how you ruled each one out, because this is the part reviewers care about most.
1. **Hypothesis:** ... **Checked by:** ... **Result:** ruled out because ...
2. ...

## Root cause
The real cause, not the symptom. Ask "why?" until you reach something you could actually change.

## Fix
The change that resolved it. Link the commit.

## Prevention
What stops this recurring: a test, an alarm, a check in CI, a line in the runbook.

## What I learned
One or two sentences.

---

## Where to look when something breaks

Likely candidates, and the commands to investigate them, are in [the runbook](runbook.md#troubleshooting).
