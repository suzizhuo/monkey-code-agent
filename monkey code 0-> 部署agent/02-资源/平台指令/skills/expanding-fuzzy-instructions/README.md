# expanding-fuzzy-instructions — 模糊指令扩展技能包

自包含、可安装即用。把整个目录拷进任何运行时的 skills 目录（`~/.agents/skills/`、`~/.claude/skills/` 等）即完成部署，无外部路径依赖。

## 安装

```bash
bash install.sh                 # 装到 ~/.agents/skills（默认）
bash install.sh /path/to/skills --force   # 指定目录 / 覆盖
bash scripts/selfcheck.sh       # 装后自检
```

## 目录结构

```
expanding-fuzzy-instructions/
├── SKILL.md               # 技能主体（流程 / Iron Law / 红旗）
├── fuzzy-dispatch.md      # 判据速查 + 四个派发模板 + 记账表格式
├── scripts/
│   ├── frame_blob.sh      # 帧资产 → 单行 base64 快照
│   ├── build_dispatch.sh  # 组装模糊子代理派发 prompt
│   ├── ledger.py          # 五列记账 append / terminate
│   └── selfcheck.sh       # 包自检
├── assets/
│   └── frame/preset.yml   # 运行时口径帧（自包含，md5 4116e7e205adc85e98a824fb11084e0b）
├── references/
│   └── architecture.md    # 设计文档：拓扑 / 上下文卫生 / 裁定外包 / 降级路径
├── test-scenarios.md      # C1-C8 判据 + S1-S6 测试场景
├── baseline-log.md        # 实测记录（RED/GREEN/REFACTOR 全程）
└── install.sh             # 安装脚本
```

## 使用（三步命令）

```bash
# 1. 接线人原话落文件（逐字，不改写）
echo "把那个搞一下，快点" > /tmp/original.txt

# 2. 组装派发 prompt（帧快照自动内嵌，无说明）
bash scripts/build_dispatch.sh --original /tmp/original.txt --boundary "只读优先/8分钟" --out /tmp/dispatch.txt

# 3. 派发（subagent 工具；被 depth 挡回时走 workflow 通道），记账：
python3 scripts/ledger.py append /tmp/ledger.md "模糊子代理" "初始清单N条+原话表" "建立基线" "裁定列随表返回"
```

完整流程见 `SKILL.md`，模板细节见 `fuzzy-dispatch.md`。

## 帧资产说明

`assets/frame/preset.yml` 是运行时口径帧的打包快照（48KB），派发时由 `frame_blob.sh` 单次编码后随 prompt 下发，子代理自行解码继承。包内自包含，不依赖任何机器本地路径。如需整包更新：替换该文件后跑 `bash scripts/selfcheck.sh` 确认。

## 测试与证据

- `test-scenarios.md`：合规定义 C1-C8 + 六个压力场景
- `baseline-log.md`：无技能基线 5/5 翻车、带技能合规运行、五轮 REFACTOR 记录与已知残留
