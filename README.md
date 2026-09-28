# 足球嘉年华赛事管理系统

一个使用 Python 和 Tkinter 开发的离线足球赛事管理工具，面向校园足球活动、院系杯赛和足球嘉年华。系统在本机保存数据，无需联网或安装第三方 Python 包。

## 主要功能

- 人员档案：新增、修改、批量导入、搜索和多选删除。
- 团体赛事：队伍管理、赛程安排、比赛状态、比分、出场名单和积分排名。
- 单项赛事：支持得分赛、计时赛和对抗赛，可自行添加赛事、批量报名、录入成绩和计算晋级。
- 球员表现：逐场记录进球、助攻、评分和备注，汇总个人表现。
- 综测计算：自定义加分规则，一键计算个人明细和全体综测总表。
- 数据导出：导出 CSV、Excel，并支持 JSON 数据备份。
- 数据保护：自动保存上一版本，检测多个窗口同时修改，异常数据只读启动。

## 运行环境

- Windows 10 或 Windows 11
- Python 3.8 及以上版本
- Python 自带的 Tkinter

项目不依赖第三方 Python 库。

## 快速开始

```powershell
git clone https://github.com/gxy74049-sketch/y0ng.git
cd y0ng
python main.py
```

Windows 用户也可以双击 `启动系统.bat`。

第一次启动时，程序会在项目目录生成 `football_carnival.json`。该文件包含本机录入的人员和赛事数据，已被 Git 忽略，不会在正常提交时上传。

## 示例数据

仓库提供 `football_carnival.example.json`，其中只包含虚构人员。需要体验示例时：

1. 确认程序已关闭。
2. 将 `football_carnival.example.json` 复制并重命名为 `football_carnival.json`。
3. 运行 `python main.py`。

请不要把真实学生信息、联系方式或正式赛事数据提交到公开仓库。

## 数据与备份

- 当前数据：`football_carnival.json`
- 最近一次保存前的版本：`football_carnival.json.previous`
- 手动备份：`football_carnival_backup_日期时间.json`

这些运行数据均不会提交到 Git。重要赛事节点仍建议单独保存备份。

## 测试

```powershell
python -m unittest test_football test_audit -v
```

测试使用临时数据，不会修改正式赛事文件。

## 项目结构

| 文件 | 用途 |
| --- | --- |
| `main.py` | 稳定的程序启动入口 |
| `交运杯足球嘉年华管理系统.py` | 主窗口、数据层与赛事业务 |
| `football_workspace.py` | 赛事总览与球员表现档案 |
| `football_controls.py` | 多选组件与综测规则界面 |
| `football_events.py` | 自定义赛事类型与添加赛事窗口 |
| `football_scoring.py` | 综测规则与统一计算逻辑 |
| `test_football.py` | 核心功能回归测试 |
| `test_audit.py` | 缺陷复现与数据安全测试 |

更多操作说明见 [优化方案与使用说明](./优化方案与使用说明.md)，修复记录见 [缺陷检查与修复报告](./缺陷检查与修复报告.md)。

## 使用范围

当前版本定位为单机离线工具。同一份 JSON 数据不适合由多人或多个窗口同时编辑；需要多人协作时，建议后续迁移到带账号和数据库的 Web 版本。

