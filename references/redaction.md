# What redaction catches, and what it misses

Redaction runs on your machine before any submission reaches a model. It
replaces student identity with stable tokens and keeps the token-to-name map
local. Read this before you send anything to a cloud model.

## What it catches

| Layer | Catches |
| --- | --- |
| Roster match | The author and classmates, by name, student ID, or email |
| Structured patterns | Emails, phone numbers, cued student IDs, personal profile URLs |
| Container | Names in filenames, and the document author, title and comments fields |
| Review flags | People named in personal contexts inside the essay |

Output files are named by token, so a filename cannot carry a name. The map
file and the review file are written with owner-only permissions and never
enter a model's context.

## What it misses

Five limits matter. Each one is real, and each one will surprise you if you
assume otherwise.

### Pseudonymization is not anonymization

Redaction removes names. It cannot remove the facts that identify a person.
"My grandmother's escape from Saigon in 1975" names nobody and still tells
anyone who knows the class who wrote it. The warning pass flags passages like
this and quotes them for you. It does not remove them, and it does not catch
every one.

### A protected name can shield a student's surname

You pass your own name so the tool does not redact you. When your name matches
a student's surname exactly — you are Dr. Chen and Samantha Chen is in the
class — the bare word "Chen" is left alone wherever it appears. Her full name
is still tokenized. Her surname on its own is not.

### The review flags are a helper, not a guarantee

The tool flags names that appear in personal contexts: "my landlord, Elena
Vasquez", "I spoke with Elena", "Professor Alvarez". It deliberately misses
"Elena raised me" and "According to Elena", because no rule separates those
from ordinary academic prose without also flagging every scholar the essay
cites. Inspect the review file locally, outside any model conversation. Treat
it as a prompt to look, not a complete list.

### A cited scholar's name is treated as subject matter

When a name appears as a year citation anywhere in an essay — "Gillespie
(2014)" — the tool stops flagging that name from weaker signals such as a
possessive or a quotation. This keeps a reflective essay from raising a warning
in every paragraph. The cost is that a private individual who shares a surname
with a cited scholar may not be flagged by those weaker signals. Stronger
signals, such as a family word or a title, still flag them.

### Subject matter is kept on purpose

Places, historical figures, statute numbers, patent numbers, and the dates an
essay is about are left alone. Removing them would make the work impossible to
grade. A right-to-repair essay keeps DMCA 1201 and its patent numbers. A
platform-governance essay keeps its cited rules and terms pages. That is
deliberate, not an oversight.

## What this means for you

Inspect the review file locally before you send anything to a cloud model. It lists the
names flagged for your judgement, the passages that may identify their author,
and every bare surname the tool substituted.

Redaction fails closed. A non-zero exit means the batch is not safe to send,
and there is no flag that overrides it.

Your institution's policy decides whether student work may leave this machine.
This tool does not make that decision for you.
