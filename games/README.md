# games/

每个游戏一个目录。约定：

```
<game>/
├── bridge/      # 游戏内执行器：mod / 注入 / 像素
├── adapter/     # Python：把 bridge 的状态翻译成 core 契约
├── docs/        # 这个游戏的决策文档、踩坑
├── runtime/     # 运行时文件（gitignore）
└── data/        # 回放、账本（gitignore）
```

第一个：`backpack-hero/`（从 `D:\projects\backpack-hero-aipeiwo` 迁入）。
