# 介孔导电框架文献 RSS

这是一个面向介孔导电/共轭 MOF、COF、晶态多孔框架组装与电化学合成的个人学术 RSS。它每天从 OpenAlex、Crossref 和 arXiv 检索北京时间昨日 00:00–23:59 发表的候选论文，跨来源去重、按研究相关性评分，再生成标准 RSS 2.0 日报。

RSS 每天只新增一个日报条目，日报内部汇总全部达标论文，不设置必须凑够 5 篇的要求。若昨日没有带 DOI 且达到门槛的论文，也会发布一条 0 篇日报。每篇收录论文均显示 DOI，并通过 GitHub Models 生成基于题目和摘要的中文题目、内容介绍、方法概述和课题相关性说明。

## [逐期日报归档](issues/README.md)

每次生成 RSS 时，同步在 `issues/<年份>/` 下保存一份独立 Markdown 日报，例如 `issues/2026/2026-07-21.md`。`issues/README.md` 是按年份、月份排列的总目录，结构参考 `ruanyf/weekly`。Markdown 归档永久保留，不受 RSS 最近 100 期显示上限影响。

## 当前研究画像

- 材料：导电 MOF、共轭 MOF、二维 MOF、导电 COF/共轭 COF；
- 目标：晶态多孔框架的介孔化和分级孔构筑；
- 机制：胶束/模板导向组装、界面组装、限域结晶、成核与生长；
- 前沿主线：介孔导电 MOF 的电化学合成、电沉积和电结晶；
- 关注网络：赵东元、李晓民及复旦大学相关成果。

研究词、排除词、评分门槛和每日篇数都在 `config.json` 中修改。

## 本地运行

环境要求：Python 3.7 或更高版本，不需要安装第三方依赖。

在 PowerShell 中运行：

```powershell
Set-Location D:\Codexlocal\mesoporous-framework-rss
.\run.ps1
```

生成文件：

- `public/feed.xml`：RSS 阅读器订阅文件；
- `public/papers.json`：历史论文和评分记录；
- `public/digests.json`：每日汇总条目及其论文内容；
- `public/candidate-review.json`：最高分候选及逐维得分，包括被淘汰记录，用于校准；
- `public/last-run.json`：本次运行统计及数据库错误。
- `issues/README.md`：可在 GitHub 中浏览的逐期日报总目录；
- `issues/<年份>/<日期>.md`：每期完整中文日报。

本地预览：

```powershell
.\serve.ps1
```

浏览器或 RSS 阅读器中使用 `http://localhost:8000/feed.xml`。关闭该 PowerShell 窗口后，本地地址将停止服务。

## 筛选逻辑

评分满分 100：

| 维度 | 上限 |
|---|---:|
| 主题匹配 | 35 |
| 介孔化、组装和电化学方法价值 | 25 |
| 创新交叉程度 | 15 |
| 关注作者/机构 | 10 |
| 实验可借鉴性 | 10 |
| 归档价值 | 5 |

主题匹配低于 10 分会被淘汰。`MOF-derived carbon`、非晶多孔碳等常见偏题内容会受到额外扣分。缺少 DOI 的记录不会进入日报。建议人工检查约一周后再调整 `minimum_score` 和关键词。

## 中文介绍

云端工作流使用 GitHub Models，并只向模型发送论文题目、摘要、期刊、DOI、命中主题和相关性评分。提示词要求模型不得虚构实验条件、数值或结论。工作流权限限定为 `models: read`；无需在仓库保存单独的模型 API 密钥。如果模型服务临时不可用，程序会使用证据受限的中文回退介绍并在 `last-run.json` 中记录错误。

## 自动更新与公开订阅

`.github/workflows/update-feed.yml` 已提供北京时间每天 08:30 更新的 GitHub Actions 模板。

1. 将本目录作为 GitHub 仓库推送；
2. 在仓库 Settings → Pages 中确认 Source 为 `GitHub Actions`；本项目的工作流会直接发布 `public/`；
3. 将 `config.json` 中 `feed.link` 改成最终的公开 `feed.xml` 地址；
4. 手动运行一次 `Update literature RSS`，确认 Actions 有提交权限且 `public/feed.xml` 可访问。

公开发布前不要在配置中填写 API 密钥或私人邮箱。`mailto` 只用于向 OpenAlex/Crossref 标识礼貌请求，可留空。

## 测试

离线测试不会访问网络：

```powershell
python -m unittest discover -s tests -v
```

测试数据包含一篇高相关论文和一篇 `MOF-derived carbon` 噪声论文，用于验证评分、排除和 RSS XML 格式。

## 已知边界

- 中文介绍只依据数据库提供的题目和摘要，不等同于全文精读；
- Crossref 有些记录只有元数据，没有摘要；
- 预印本和正式论文优先通过 DOI、arXiv ID、OpenAlex ID及标准化标题去重，但标题变化较大时仍可能重复；
- 免费 API 偶尔会限流，失败会记录在 `last-run.json`，其他来源仍会继续运行；
- GitHub Models 属于公开预览并有调用限额；本流程每篇论文只生成一次中文介绍，并提供失败回退；
- GitHub Pages 是公开地址。如果研究画像或历史记录需要保密，应改用私有服务器或只在本地订阅。
