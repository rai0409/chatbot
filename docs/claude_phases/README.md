# Claude Code phase prompts — chatbot

このディレクトリの各Phaseは一責務。前PhaseのPRをCI検証・レビュー・main取込後、次を実行する。既存WSLのdirty/untrackedを壊さないため、別PCまたは各Phaseのclean cloneを推奨。

## 他PCでcloneしてPhase 1を実行

    git clone --branch feat/chatbot-v3-patch-20261022 https://github.com/rai0409/chatbot.git chatbot-phase1
    cd chatbot-phase1
    claude --permission-mode acceptEdits "$(cat docs/claude_phases/PHASE1.md)"

Phase 1がmainへ取り込まれたら、新規ディレクトリでPhase 2を実行：

    git clone --branch main https://github.com/rai0409/chatbot.git chatbot-phase2
    cd chatbot-phase2
    claude --permission-mode acceptEdits "$(cat docs/claude_phases/PHASE2.md)"

Phase 2がmainへ取り込まれたら、新規ディレクトリでPhase 3を実行：

    git clone --branch main https://github.com/rai0409/chatbot.git chatbot-phase3
    cd chatbot-phase3
    claude --permission-mode acceptEdits "$(cat docs/claude_phases/PHASE3.md)"

Claude Codeは修正前の本体コード・既存テスト・consumerを照合し、整合しない作業はBLOCKEDにする。commit/push/PRは各Promptが許可するfeature branchだけ。CI失敗・未検証のPRはmergeしない。`--permission-mode acceptEdits`は編集許可であり、シェルや外部操作には追加確認が発生し得る。
