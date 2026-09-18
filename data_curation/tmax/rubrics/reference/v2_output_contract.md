# 每个 agent 每道题必须写出的一行 JSON

写入 `/tmp/v2_salvage.jsonl`,**每判完一道立刻追加一行**(不是批次结束才写)。

```json
{
  "task_id":   "task_000123_abcdef01",
  "verdict":   "CLEAN | VERIFIER-TOO-WEAK | INSTR-VERIFIER-MISMATCH |
                INSTR-ENV-MISMATCH | ANSWER-LEAK | INSTR-AMBIGUOUS |
                TASK-TRIVIAL | OTHER",
  "confidence": 1-10,

  "evidence":  "决定判决的那一行,原文引用",
  "evidence_file": "setup.sh | tests/test.sh | instruction.md",
  "evidence_line": 42,

  "checks": {
    "reachable_reference":     "路径 或 null",
    "reference_guarded":       "islink/realpath/st_ino 等出现在判分器里则 true",
    "preplaced_value":         "字面量 或 null(前提不算,只填真泄漏)",
    "unenforced_requirement":  "指令要求但判分器不验的 或 null",
    "shape_only_assertion":    true/false,
    "nondeterministic_reward": true/false
  },

  "files_read": ["instruction.md", "setup.sh", "tests/test.sh"],
  "verifier_expected_answer_from": "recomputed | hardcoded-literal |
                                    in-image-program | agent-self-report | shape-only",
  "model_reported": "判这道题时自报的模型名",
  "agent": "b1-07",
  "ts": 1756500000
}
```

## 为什么每个字段都不能省

| 字段 | 没有它会怎样 |
|---|---|
| `evidence` + `evidence_file` + `evidence_line` | 判决**不可复核**。上一轮 272/273 个泄漏判决都引了具体路径,这是它们可信的唯一原因;而 12 个漏掉的,恰恰是评委写了段说理但没指向任何一行 |
| `checks.*` **六项全填** | prompt 里那句"判 CLEAN 前先自查"如果只是内心活动,**没有任何证据它做了**。写成字段就变成必须回答 |
| `reference_guarded` | 区分"没有参考实现"和"有但有防护"。这两个都会让白嫖测试判 blocked,但含义完全不同 |
| `verifier_expected_answer_from` | 上一轮最大的教训:评委把 `in-image-program` 当成了强判分器。**强制它说出标准答案的来源**,这个盲点就无处可藏 |
| `files_read` | 一个没读 `setup.sh` 的评委不可能发现泄漏。它自报读了什么,和它的判决对不对得上,是可查的 |
| `model_reported` | 观测到过静默降级(12 个 agent 在 input_tokens=10968 处掉档)。自报不可全信,但不记就完全没有 |
| `agent` + `ts` | 崩溃后能定位丢在哪、谁判的 |

## 硬性规则

1. **判完一道写一道。** agent 崩了最多丢在飞的那一道,不是整批 10 道。
2. **开工前先 grep salvage 文件跳过已判的。** 重启不重复判。
3. `verdict != CLEAN` 时,`evidence` 必须引用**真实存在的一行**,不能是概括。
4. `checks` 六项**不允许留空**。不确定就填保守值并在 evidence 里说明。
