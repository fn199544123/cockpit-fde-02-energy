<p align="center">
  <img src="assets/cockpit-logo.png" width="280" alt="cockpit 驾驶舱" />
</p>

<p align="center"><strong>cockpit 驾驶舱 · AI 开发实作</strong></p>
<h1 align="center">把业务需求，做成看得见、用得上的系统。</h1>
<p align="center">从需求拆解到系统实现，记录 AI 开发的真实过程。</p>
<h2 align="center">车间能耗监控看板</h2>
<p align="center">喜文FDE案例系列</p>
<p align="center">制造业 · 分工段能耗统计与超标预警 · 业务演示系统</p>
<p align="center"><a href="https://resume.fangnan.club/cockpit/">了解 cockpit</a> · <a href="https://resume.fangnan.club/">认识作者</a></p>


## 认识作者，一起把想法做出来

**我是方楠，智能体工程师 / 全栈工程师。**

专注 AI 编程、智能体开发与业务系统落地，具备 Python、前后端开发、数据工程与技术交付经验。从需求梳理、架构设计到系统实现，关注技术怎样解决具体业务问题。

这里分享我使用 cockpit 驾驶舱开展 AI 开发的项目：需求怎样拆、系统怎样做、业务流程怎样验证。希望这些可查看、可复现的实现，为你的下一个项目提供参考。

**有相似需求？欢迎交流业务场景、AI 开发实践与项目合作。**

| 了解我 / 联系我 | 入口 |
| :--- | :--- |
| 个人主页 | [方楠 · 个人简历](https://resume.fangnan.club/) |
| 电话 / 微信 | 16607557430 |
| 合作邮箱 | [16607557430@163.com](mailto:16607557430@163.com) |
| 抖音 | 智效上门AI解决方案 · 抖音号：74759905847 |
| cockpit 介绍 | [了解 cockpit 驾驶舱](https://resume.fangnan.club/cockpit/) |

<p align="center">
  <img src="assets/douyin-qr-placeholder.svg" width="200" alt="抖音二维码待提供；此处为不可扫码的版式占位" />
</p>
<p align="center"><strong>关注我的抖音，看需求如何一步步变成系统。</strong><br />真实开发过程 · 系统操作演示 · 项目复盘</p>

作者介绍与联系方式整理自[个人简历网站](https://resume.fangnan.club/)。抖音二维码原图待补。

<p align="center"><strong>喜欢这类项目，欢迎 Star 收藏，也欢迎通过 Issues 一起完善。</strong></p>

---

## 车间能耗监控看板解决什么问题？

面向制造业，围绕“分工段能耗统计与超标预警”提供可操作的演示系统。当前交付状态：**演示原型；中台业务验收尚未完成**。

## 系统架构

```text
浏览器 → Django 路由与视图 → 业务模型 / 规则 → SQLite
浏览器 ← HTML 模板 / JSON ← 查询与统计结果
```

cockpit 用于 AI 开发过程，不是此系统运行的必需服务。本系统没有因为使用 AI 开发而自动接入运行时大模型。

## 本地运行

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python scripts/seed_demo.py
python manage.py runserver 127.0.0.1:8000
```

打开 http://127.0.0.1:8000/ 。种子脚本仅用于新建演示库，可能重置演示记录，勿在业务数据库上执行。Python 3.10+；依赖版本见 requirements.txt。

本仓库不含原运行数据库或部署密钥。`DJANGO_SECRET_KEY` 可通过环境变量设置；本地未设置时自动生成临时密钥。`DJANGO_DEBUG` 默认用于本地演示，正式部署须设为 `0` 并通过 `DJANGO_ALLOWED_HOSTS` 配置主机。此仓库未完成生产部署安全验收。

## 代码目录

```text
energy/   Django 配置（发布版通过环境变量读取部署参数）
board/     模型、视图、表单与数据库迁移
templates/ 页面模板
static/    本地样式与脚本
scripts/seed_demo.py  脱敏演示数据
manage.py  管理入口
README.md      项目说明
LICENSE        MIT 许可证
```

## 功能入口

- `/`
- `/manage/sections/`
- `/manage/sections/new/`
- `/manage/energies/`
- `/manage/energies/new/`
- `/manage/readings/`
- `/manage/readings/new/`
- `/manage/readings/batch/`
- `/manage/analysis/`
- `/manage/efficiency/`
- `/manage/alerts/`

## 运行边界

本项目使用脱敏演示数据。页面与接口用于业务操作演示，未承诺真实硬件、企业系统对接或生产级多租户权限。具体业务规则以源码为准。


## 验证范围

发布前在独立导出目录验证启动与入口访问；实际结果随发布回执记录。业务验收状态与代码发布状态分开管理。原有测试记录属于历史开发验证，不代表生产环境验收。

## 交流与贡献

使用问题请在本仓库 Issues 提供环境、复现步骤与脱敏截图。欢迎提交改进建议或 Pull Request；业务交流见顶部公开联系方式。如果对你有帮助，欢迎 Star 收藏。

## 项目地址

- GitHub: https://github.com/fn199544123/cockpit-fde-02-energy
- 码云: https://gitee.com/xiwenfde/cockpit-fde-02-energy

## 许可证

本项目原创代码采用 [MIT](LICENSE)，允许在遵守许可声明的前提下使用、修改与商用。第三方组件适用各自许可证；cockpit 品牌素材用于项目归属展示，不代表商标授权或官方背书。

演示数据均为虚构编号与通用业务信息，不代表真实客户经营数据。

---

<p align="center"><strong>cockpit 驾驶舱 · 喜文FDE案例系列</strong></p>
