#!/usr/bin/env bash
# Grader: reward 1 iff every FAIL_TO_PASS passes and the PASS_TO_PASS sample
# holds. The graded tests are materialized here, from a patch the agent has
# never seen: the test surface is first restored to HEAD, so nothing the agent
# planted in a graded test, a fixture beside it, or a conftest that feeds it
# survives to be graded.
set -uo pipefail

# The repo is wherever the image put it: the shallowest .git whose HEAD is the
# task's base commit. Depth-limited so a stray vendored .git cannot win.
find_repo() {
  want="$1"
  for d in $(find / -maxdepth 2 -name .git -type d 2>/dev/null | sort); do
    r="$(dirname "$d")"
    if [ "$(git -C "$r" rev-parse HEAD 2>/dev/null)" = "$want" ]; then
      echo "$r"; return 0
    fi
  done
  # No fallback. Grading "any repo at all" is worse than not grading: the old
  # fallback ran the graded tests against whatever .git turned up first, and
  # whatever it scored was recorded as a fact about this task.
  echo "no repository at base commit $want" >&2
  return 1
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p /logs/verifier
# Fail closed. A grader with no repository at the task's base commit has
# nothing to say about the task, and saying 0 without saying why is how a
# broken box gets recorded as a wrong fix.
R="$(find_repo 2e543b45e81cf76af9ce354fd34dd3e1ad428b21)" || {
  echo "error=no_repo_at_base_commit" > /logs/verifier/pytest_rc.txt
  echo 0 > /logs/verifier/reward.txt
  exit 0
}
cd "$R"
# `HEAD --`, never a bare `--`: `git checkout -- <path>` restores from the
# INDEX, so an agent who edited a graded test and `git add`ed it would have
# their own version restored by the very step meant to undo it.
while IFS= read -r f; do
  [ -z "$f" ] && continue
  git checkout -q HEAD -- "$f" 2>/dev/null || rm -f -- "$f"
done <<'TPFILES'
tests/test_docformatter.py
tests/test_format_docstring.py
TPFILES
# And the whole test surface, not only the paths test_patch happens to touch:
# a graded test usually imports helpers, fixtures and a conftest that the patch
# never mentions, and each of those is a place to make a failing test pass.
# Restricted to what a source fix never touches — measured over all 1,093 gold
# patches: none touches a tests/ tree, a conftest.py or pytest.ini, while 34
# touch pyproject.toml, 7 tox.ini and 4 setup.cfg, which is why those three are
# reported below rather than restored. `git clean` without -x, so the image's
# own ignored artifacts (a generated _version.py, an egg-info) stay: removing
# them leaves a repository that cannot import itself.
# The test surface, listed out of HEAD and restored in one pass. Not
# `git checkout -- :(glob)...`: one pathspec that matches nothing makes git
# refuse the WHOLE command, so in any repository without a test/ directory the
# entire reset silently did nothing — which is how this was found. Listing from
# HEAD also restores a graded test the agent deleted outright, which a worktree
# listing would have missed.
git ls-tree -r -z --name-only HEAD \
  | grep -zE '(^|/)(tests?|testing)/|(^|/)conftest\.py$|(^|/)pytest\.ini$' \
  | xargs -0 -r git checkout -q HEAD -- 2>/dev/null
# and anything the agent ADDED in there that HEAD never had. --exclude-standard
# so the image's own ignored artifacts (a generated _version.py, an egg-info)
# stay: removing those leaves a repository that cannot import itself.
git ls-files -z --others --exclude-standard \
  | grep -zE '(^|/)(tests?|testing)/|(^|/)conftest\.py$|(^|/)pytest\.ini$' \
  | xargs -0 -r rm -f -- 2>/dev/null
touched="$(git diff --name-only HEAD -- ':(glob)**/pyproject.toml' ':(glob)**/setup.cfg' ':(glob)**/tox.ini' 2>/dev/null)"
if ! git apply --whitespace=nowarn "$HERE/test.patch"; then
  echo "test_patch failed to apply" >&2
  echo "error=test_patch_did_not_apply" > /logs/verifier/pytest_rc.txt
  echo 0 > /logs/verifier/reward.txt
  exit 0
fi
# Reward and diagnosis are two different questions and pytest answers both:
# the reward is "1 iff every chunk exited 0" and does not change here, but the
# exit codes are recorded next to it because they say WHY a chunk was not 0 —
# 1 tests failed, 4 usage error (an id pytest cannot address), 5 nothing
# collected, >=128 killed by a signal (137 is the OOM killer). Without them a
# grader that never ran a test and a fix that did not work write the same 0.
# `worst` is the numeric maximum, which for those codes is also the most
# severe: 0 < 1 < 4 < 5 < 137 runs from clean to killed.
fail=0
worst=0
chunk_i=0
rcs=""
# Every chunk's output is kept as well as printed. The reward says whether all
# of them passed and the exit codes say why not, but neither says WHICH tests
# failed — and that is the question a broken contract answers: an agent that
# passes the tests mirroring the issue and fails a disjoint cluster has solved
# what it was asked for and been graded on something else. -rA already prints
# one line per test; this keeps them.
PYOUT="$(mktemp)"
run_chunk() {
  pytest --no-header -rA --tb=line --color=no -p no:cacheprovider -q "$@" 2>&1 | tee -a "$PYOUT"
  rc=${PIPESTATUS[0]}
  [ "$rc" -eq 0 ] || fail=1
  [ "$rc" -gt "$worst" ] && worst="$rc"
  rcs="${rcs}chunk_${chunk_i}=${rc}
"
  chunk_i=$((chunk_i + 1))
  return 0
}
run_chunk 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_multi_paragraph_description[args0]'  # FAIL_TO_PASS
run_chunk tests/test_docformatter.py::TestMain::test_diff_with_nonexistent_file tests/test_docformatter.py::TestMain::test_io_error_exit_code 'tests/test_format_docstring.py::TestFormatDocstring::test_format_docstring[args0]' 'tests/test_format_docstring.py::TestFormatDocstring::test_format_docstring_with_summary_that_ends_in_quote[args0]' 'tests/test_format_docstring.py::TestFormatDocstring::test_format_docstring_with_bad_indentation[args0]' 'tests/test_format_docstring.py::TestFormatDocstring::test_format_docstring_with_too_much_indentation[args0]' 'tests/test_format_docstring.py::TestFormatDocstring::test_format_docstring_with_trailing_whitespace[args0]' 'tests/test_format_docstring.py::TestFormatDocstring::test_format_docstring_with_empty_docstring[args0]' 'tests/test_format_docstring.py::TestFormatDocstring::test_format_docstring_with_no_period[args0]' 'tests/test_format_docstring.py::TestFormatDocstring::test_format_docstring_with_single_quotes[args0]' 'tests/test_format_docstring.py::TestFormatDocstring::test_format_docstring_with_single_quotes_multi_line[args0]' 'tests/test_format_docstring.py::TestFormatDocstring::test_format_docstring_leave_underlined_summaries_alone[args0]' 'tests/test_format_docstring.py::TestFormatLists::test_format_docstring_should_ignore_numbered_lists[args0]' 'tests/test_format_docstring.py::TestFormatLists::test_format_docstring_should_ignore_parameter_lists[args0]' 'tests/test_format_docstring.py::TestFormatLists::test_format_docstring_should_ignore_colon_parameter_lists[args0]' 'tests/test_format_docstring.py::TestFormatLists::test_format_docstring_should_leave_list_alone[args0]' tests/test_format_docstring.py::TestFormatWrap::test_unwrap_summary 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_wrap[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_weird_indentation_and_punctuation[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_description_wrapping[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_should_ignore_multi_paragraph[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_should_ignore_doctests[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_should_ignore_doctests_in_summary[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_should_maintain_indentation_of_doctest[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_force_wrap[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_summary_only_and_wrap_and_tab_indentation[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_for_multi_line_summary_alone[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_for_one_line_summary_alone_but_too_long[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_inline_links[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_short_inline_link[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_inline_link_retain_spaces[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_target_links[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_simple_link[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_short_link[args0]' 'tests/test_format_docstring.py::TestFormatWrap::test_format_docstring_with_class_attributes[args0]' 'tests/test_format_docstring.py::TestFormatStyleOptions::test_format_docstring_with_no_post_description_blank[args0]' 'tests/test_format_docstring.py::TestFormatStyleOptions::test_format_docstring_with_pre_summary_newline[args0]' 'tests/test_format_docstring.py::TestFormatStyleOptions::test_format_docstring_make_summary_multi_line[args0]' 'tests/test_format_docstring.py::TestFormatStyleOptions::test_format_docstring_pre_summary_space[args0]' 'tests/test_format_docstring.py::TestStripDocstring::test_strip_docstring[args0]' 'tests/test_format_docstring.py::TestStripDocstring::test_strip_docstring_with_single_quotes[args0]' 'tests/test_format_docstring.py::TestStripDocstring::test_strip_docstring_with_empty_string[args0]' 'tests/test_format_docstring.py::TestStripDocstring::test_strip_docstring_with_raw_string[args0]' 'tests/test_format_docstring.py::TestStripDocstring::test_strip_docstring_with_unicode_string[args0]' 'tests/test_format_docstring.py::TestStripDocstring::test_strip_docstring_with_unknown[args0]' 'tests/test_format_docstring.py::TestStripDocstring::test_strip_docstring_with_double_quotes[args0]'  # PASS_TO_PASS sample
# One line per test id, tab-separated: PASSED/FAILED/ERROR carry the node id in
# pytest's summary, and XFAIL/XPASS do too and are neither a pass nor a plain
# failure. SKIPPED is deliberately absent — pytest reports it as `file:line`,
# with no node id to attribute it to — so an id that appears in neither the
# graded set's results nor anywhere else was not run, which is itself the
# finding: an F2P that is skipped makes its chunk exit 0 and buys reward 1
# without ever having been a test.
grep -E '^(PASSED|FAILED|ERROR|XFAIL|XPASS) ' "$PYOUT" \
  | sed -E 's/^PASSED ([^ ]+).*/\1\tPASS/; s/^FAILED ([^ ]+).*/\1\tFAIL/; s/^ERROR ([^ ]+).*/\1\tERROR/; s/^XFAIL ([^ ]+).*/\1\tXFAIL/; s/^XPASS ([^ ]+).*/\1\tXPASS/' \
  > /logs/verifier/test_results.txt
rm -f "$PYOUT"
{ printf '%s' "$rcs"
   echo "worst=$worst"
   for f in $touched; do echo "config_touched=$f"; done
   true; } > /logs/verifier/pytest_rc.txt
if [ "$fail" -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit 0
