# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""mini-swe-agent's text-based prompts, copied verbatim, for ``miniswe.py``.

Source: github.com/SWE-agent/mini-swe-agent, tag v1.17.5, commit
68d367bf0ccf40ad59a07101a94c21b66e7798ff.

  - ``SYSTEM_TEMPLATE``, ``_UPSTREAM_INSTANCE_TEMPLATE``,
    ``ACTION_OBSERVATION_TEMPLATE``, ``FORMAT_ERROR_TEMPLATE`` and ``ENV`` are
    ``src/minisweagent/config/default.yaml`` (``agent.*`` and
    ``environment.env``). Its templates are identical to ``config/mini.yaml``,
    which is what Terminal-Bench's own mini-swe-agent adapter
    (``terminal_bench/agents/installed_agents/mini_swe_agent``: ``mini -m <model>
    -t <instruction> -y --exit-immediately``) runs.
  - ``TIMEOUT_TEMPLATE`` and ``ACTION_REGEX`` are the ``AgentConfig`` defaults in
    ``src/minisweagent/agents/default.py``, which default.yaml does not override.

v1.17.5 is the last release whose text protocol is a ```bash fence with a
per-command timeout message. From v2.0 the default is tool calling (which is
what ``vanillux_prompts.py`` ports), and v2's text-based config
(``mini_textbased.yaml``) renames the fence to ``mswea_bash_command`` and drops
the timeout template.

These are Jinja2 templates rendered with ``StrictUndefined``, as upstream does.
Two changes: ``INSTANCE_TEMPLATE`` adds one rule after upstream's rule 3 (see
``_BACKGROUND_RULE``), and ``FORMAT_ERROR_TEMPLATE`` drops upstream's "If you
want to end the task, please issue ... echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
without any other command. Else, please format ..." (now "Please format ..."):
in run 1024 it steered the policy into submitting right after a format error
(7 of 9 such eval submits failed). Do not edit the other upstream strings: the
point of this harness is that a policy sees mini-swe-agent's own prompt.
"""

SYSTEM_TEMPLATE = """\
You are a helpful assistant that can interact with a computer.

Your response must contain exactly ONE bash code block with ONE command (or commands connected with && or ||).
Include a THOUGHT section before your command where you explain your reasoning process.
Format your response as shown in <format_example>.

<format_example>
Your reasoning and analysis here. Explain why you want to perform the action.

```bash
your_command_here
```
</format_example>

Failure to follow these rules will cause your response to be rejected.
"""

_UPSTREAM_INSTANCE_TEMPLATE = """\
Please solve this issue: {{task}}

You can execute bash commands and edit files to implement the necessary changes.

## Recommended Workflow

This workflows should be done step-by-step so that you can iterate on your changes and any possible problems.

1. Analyze the codebase by finding and reading relevant files
2. Create a script to reproduce the issue
3. Edit the source code to resolve the issue
4. Verify your fix works by running your script again
5. Test edge cases to ensure your fix is robust
6. Submit your changes and finish your work by issuing the following command: `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.
   Do not combine it with any other command. <important>After this command, you cannot continue working on this task.</important>

## Important Rules

1. Every response must contain exactly one action
2. The action must be enclosed in triple backticks
3. Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
   However, you can prefix any action with `MY_ENV_VAR=MY_VALUE cd /path/to/working/dir && ...` or write/load environment variables from files

<system_information>
{{system}} {{release}} {{version}} {{machine}}
</system_information>

## Formatting your response

Here is an example of a correct response:

<example_response>
THOUGHT: I need to understand the structure of the repository first. Let me check what files are in the current directory to get a better understanding of the codebase.

```bash
ls -la
```
</example_response>

## Useful command examples

### Create a new file:

```bash
cat <<'EOF' > newfile.py
import numpy as np
hello = "world"
print(hello)
EOF
```

### Edit files with sed:

{%- if system == "Darwin" -%}
<important>
You are on MacOS. For all the below examples, you need to use `sed -i ''` instead of `sed -i`.
</important>
{%- endif -%}

```bash
# Replace all occurrences
sed -i 's/old_string/new_string/g' filename.py

# Replace only first occurrence
sed -i 's/old_string/new_string/' filename.py

# Replace first occurrence on line 1
sed -i '1s/old_string/new_string/' filename.py

# Replace all occurrences in lines 1-10
sed -i '1,10s/old_string/new_string/g' filename.py
```

### View file content:

```bash
# View specific lines with numbers
nl -ba filename.py | sed -n '10,20p'
```

### Any other command you want to run

```bash
anything
```
"""

ACTION_OBSERVATION_TEMPLATE = """\
<returncode>{{output.returncode}}</returncode>
{% if output.output | length < 10000 -%}
<output>
{{ output.output -}}
</output>
{%- else -%}
<warning>
The output of your last command was too long.
Please try a different command that produces less output.
If you're looking at a file you can try use head, tail or sed to view a smaller number of lines selectively.
If you're using grep or find and it produced too much output, you can use a more selective search pattern.
If you really need to see something from the full command's output, you can redirect output to a file and then search in that file.
</warning>
{%- set elided_chars = output.output | length - 10000 -%}
<output_head>
{{ output.output[:5000] }}
</output_head>
<elided_chars>
{{ elided_chars }} characters elided
</elided_chars>
<output_tail>
{{ output.output[-5000:] }}
</output_tail>
{%- endif -%}
"""

FORMAT_ERROR_TEMPLATE = """\
Please always provide EXACTLY ONE action in triple backticks, found {{actions|length}} actions.
Please format your response exactly as follows:

<response_example>
Here are some thoughts about why you want to perform the action.

```bash
<action>
```
</response_example>

Note: In rare cases, if you need to reference a similar format in your command, you might have
to proceed in two steps, first writing TRIPLEBACKTICKSBASH, then replacing them with ```bash.
"""

TIMEOUT_TEMPLATE = (
    "The last command <command>{{action['action']}}</command> timed out and has been killed.\n"
    "The output of the command was:\n"
    "{% if output | length < 10000 -%}\n"
    "<output>\n{{output}}\n</output>\n"
    "{%- else -%}\n"
    "<warning>Output was too long and has been truncated.</warning>\n"
    "<output_head>\n{{ output[:5000] }}\n</output_head>\n"
    "<elided_chars>{{ output | length - 10000 }} characters elided</elided_chars>\n"
    "<output_tail>\n{{ output[-5000:] }}\n</output_tail>\n"
    "{%- endif %}\n"
    "Please try another command and make sure to avoid those requiring interactive input."
)

ACTION_REGEX = r"```bash\s*\n(.*?)\n```"

ENV = {
    "PAGER": "cat",
    "MANPAGER": "cat",
    "LESS": "-R",
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
}

# Terminal-Bench adaptation. Upstream rule 3 already says every action runs in a
# new subshell. What it does not say is how to keep a server up for the
# verifier, which many Terminal-Bench tasks need and a SWE-bench patch never
# does: a foreground server never returns and is killed at the per-command
# timeout.
_RULE_3_END = (
    "   However, you can prefix any action with `MY_ENV_VAR=MY_VALUE cd "
    "/path/to/working/dir && ...` or write/load environment variables from files\n"
)
_BACKGROUND_RULE = (
    "4. A command that does not exit on its own is killed after a timeout. Start "
    "long-running processes such as servers in the background with their output "
    "redirected, e.g. `nohup your_server > /tmp/server.log 2>&1 &`, and read the "
    "log in a later action\n"
)
INSTANCE_TEMPLATE = _UPSTREAM_INSTANCE_TEMPLATE.replace(
    _RULE_3_END, _RULE_3_END + _BACKGROUND_RULE, 1
)
