# Ubuntu corpus attribution

The three columns in `annotations.csv` are adapted from *Understanding Common Ground Misalignment in Goal-Oriented Dialog: A Case-Study with Ubuntu Chat Logs* by Rupak Sarkar, Neha Srikanth, Taylor Hudson, Rachel Rudinger, Claire Bonial and Philip Resnik (2025), formatted for ConvoKit by Axel Bax. The data are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The Apache license at the repository root covers Telryn code. [Corpus documentation](https://www.convokit.cornell.edu/documentation/chatlogs.html) · [Research paper](https://arxiv.org/abs/2503.12370)

This projection contains conversation IDs and two published annotations. Telryn maps the GPT-4o flag and the human friction label to binary values. It omits transcripts, speakers and explanations. Telryn supplies a simulated batch date and a simulated ordinary review list. The source has no operational QA assignments, remediation records or measured reviewer time. The example records zero minutes for importing archived labels.

To independently reproduce the projection, download the source archive and compare the generated file. The script verifies the archive's SHA-256 before it reads the metadata.

```sh
curl -fL https://zissou.infosci.cornell.edu/convokit/datasets/ubuntu-chat-logs/ubuntu-chat-logs.zip -o ubuntu-chat-logs.zip
python3 -m examples.public_demo.rebuild ubuntu-chat-logs.zip rebuilt.csv
cmp rebuilt.csv examples/public_demo/annotations.csv
```
