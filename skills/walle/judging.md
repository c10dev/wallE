# Judging candidates with your own agent

walle makes no model call and needs no API key. You, the agent, judge. Read this file only when the user wants candidates judged.

1. Run `walle judge --dry-run`. Tell the user the item count and the estimated tokens.
   - The estimate counts only reading the batch files. Judging adds output tokens.
   - Quick mode (default): the judge reads only the batch file. Expect about two to three times the estimate.
   - Deep mode (`--deep`): the judge may open the instruction files and code to check a verdict. It finds more, and costs much more: one real run of 296 items used about 14 times the estimate. Use it only when the user asks.
   - If the expected cost is more than 50,000 tokens, ask before you go on.
2. Run `walle judge` (add `--deep` if chosen; deep also re-judges items that a quick run judged). It prints one line per batch: `<batch file> -> <answer file>`. If it prints a `judge.md` path instead, every item is cached; go to step 5. Each run makes a new export folder; answers already written stay where they are.
3. Judge each batch. A batch file holds the rubric, the answer schema, the depth, the items with file:line, and its answer file.
   - One or two batches: read each batch file yourself and write the answer file.
   - More batches: start one subagent per batch, in parallel. Give each subagent only this prompt: "Read <batch file>. Follow its rubric. Write only the JSON it asks for to the answer file it names. Do not edit any other file."
4. Run `walle judge --import --judge-name <your model id>`. It checks the answers, caches them and prints the `judge.md` path. If it reports a missing answer or id, judge that batch again and import again.
5. Read `judge.md`. Report the real conflicts and the real cost items with file:line, the line to keep and the fix. Items with keep "ask" need the user's choice; say what the choice is. Do not edit instruction files unless the user asks.
