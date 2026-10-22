# Process / Measure 执行记录

## 后续人工验收与集成授权（2026-10-22）

用户确认基础操作测试通过、当前操作未发现问题，并授权提交现有改动、合并到 main、推送 GitHub。以下各阶段的“提交待办/审批阻碍”属于实施时历史记录；本次以一个实现提交汇总这些经过审查的改动，不补造逐任务提交历史。逐项专项验收及大文件性能仍不代勾；不创建标签或 Release。

集成前重新运行完整测试：`python -m pytest -q -rs -p no:cacheprovider --tb=short`，实际 **672 passed、2 skipped in640.67s，退出码0**；两项跳过仍为 Windows 符号链接权限限制。进程外比较三份 raw 的文件名、大小及 UTC mtime ticks，前后完全相同。远端 main 与本地 main 均为 `0031a0f`，功能分支是其后代，可快进合并。

唯一工作目录：`D:\carbonfoam\infrared`。实现分支：`codex/process-measure`，起点 `bd4e899`。

遵循已批准设计和实施计划；单实现代理顺序执行，不创建 worktree，不 push/merge/tag/release，不安装依赖。`raw/*.seq` 只读。

## 阶段 I

- 任务 1：实现与验证完成，提交待办。红灯：新增测试 collection 因缺少 `measurement_models` 模块报 1 error（非 SDK/fixture 错误）。实现后同一命令 `python -m pytest tests/unit/test_measurement_stats.py -q -p no:cacheprovider`：23 passed；`git diff --check` 通过。原定提交 `feat: add validated ROI temperature statistics` 未执行成功：git add 需审批并等待；主代理已中断等待并要求先推进代码，未绕过权限。
- 任务 2：实现与验证完成，提交待办。红灯：缺少 histogram 导入，1 collection error。绿灯：`python -m pytest tests/unit/test_measurement_stats.py tests/unit/test_frame_transformer.py -q -p no:cacheprovider`，63 passed in 0.93s。待提交 models/stats/stats tests 的新增部分。
- 任务 3：实现与验证完成，提交待办。红灯：缺少 MeasurementSettings 导入，1 collection error。首次绿灯运行发现原测试将 v3 当未知版本，按计划更新为 v4；默认沙箱重跑遇 pytest 临时目录权限问题，停止后通过既有授权 require_escalated 重跑：`python -m pytest tests/unit/test_measurement_models.py tests/unit/test_selection.py tests/unit/test_export_bundle.py -q -p no:cacheprovider -x`，128 passed in 4.74s。待提交 models/selection/export_bundle 及对应 tests。
- 任务 4：实现与验证完成，提交待办。红灯：缺少 measurement_state 模块，126 deselected、1 collection error。首轮 state/viewer/selection 目标回归197 passed in 147.40s；补充两种保存、变换确认和清理所有权用例后目标回归25 passed、120 deselected in 5.64s。清理异常追加回归先见2 failed（旧store cleanup异常导致状态已替换却抛出加载失败），再实现会话保留清理所有权及重试。

每任务记录实际红灯、绿灯、提交及偏离；阶段结束提供回归证据。

接口细节：MeasurementSettings.result_view 使用 `current` / `curves`；AnalysisKey.from_inputs 是唯一规范构造入口；ViewerSession.confirm_range/reset_full 是确认范围变更与失效接线，UI 经此调用。早期 store 接口只通过 TYPE_CHECKING 引用；MeasureState.clear 不执行 IO，交还旧 store；会话作为调用方负责 cleanup。

阶段 I 完整回归：`python -m pytest tests/unit -q -rs -p no:cacheprovider`（既有授权沙箱外运行）实际为 **532 passed、2 skipped in 172.33s**。两个 skip 均为 Windows WinError 1314 无符号链接权限，未削弱测试。运行期间三份 raw 文件 stat 快照不变：长度分别 42873727、1518245059、7625825755；mtime ticks 分别 639235060000000000、639235060640000000、639235065440000000。没有读取大文件温度。`git diff --check` 通过。

阶段 I 待提交范围：measurement_models.py、measurement_stats.py、measurement_state.py、selection.py、export_bundle.py、viewer.py，以及对应 measurement/selection/export_bundle/viewer 测试和本计划/记录。由于任务间共享文件，后续 Git 权限恢复时可以按已记录功能边界补提交；当前 HEAD 仍 bd4e899，不能称为已提交。主代理规格/质量审查通过后已授权继续阶段 II，无需等待人工确认。

## 阶段 II

- 任务 5：实现与验证完成，提交待办。红灯：缺少 viewer_pages 模块，1 collection error。首次页面/鼠标目标集13 passed、128 deselected in25.24s；viewer/pages/layout全文件146 passed in171.25s。父审查发现busy刷新可能禁用Cancel，新增实际鼠标回归先见1 failed，再引入PageGroup.available_during_busy；Cancel+页面+原BUG-005鼠标目标集10 passed、132 deselected in28.66s。待提交 viewer_pages/viewer_layout/viewer 和对应测试。
- 任务 6：实现与验证完成，提交待办。红灯：pixel_geometry缺模块（1 collection error），真实Add按钮用例缺少ViewerUI.measure_panel（1 failed）。首轮panel/pixel/editor59 passed in34.42s；新增单像素和切页勾选检查后编辑目标集61 passed、3 deselected in43.69s（排除已写任务7红灯）。独立viewer全文件139 passed in298.08s。精确Axes外缘屏幕整数取整会让MouseEvent落到图外；单像素真实事件采用内部半像素边界，原点边界仍由pixel_geometry纯函数测试覆盖。
- 任务 7：实现与验证完成，提交待办。红灯：measurement_plots缺模块（1 collection error）；current_analysis_reads缺少refresh_frame（1 failed）。统计/面板/绘图聚焦集49 passed in54.05s。拖动合并扩展测试先见输入框未同步的1 failed，再强制release同步最终表单。实际渲染发现卡片/色标/长名说明布局冲突，补renderer断言并分别见红后修复；最终正常分布/剖面、长名+混合NaN实图2 passed in5.91s。

阶段 II 完整回归：`python -m pytest tests/unit -q -rs -p no:cacheprovider`，**563 passed、2 skipped in354.41s**。两个skip仍为Windows符号链接权限。最后新增的说明/刻度无重叠断言单独2 passed补验；`git diff --check`通过。主代理已实际查看PNG并通过阶段审查。截图在pytest-1250/test_render_measure_preview0/measure-distribution.png、measure-profile.png及test_measure_text_stays_outsid0/measure-long-invalid.png（系统pytest临时目录）。

阶段 II 待提交范围：viewer_pages.py、pixel_geometry.py、measurement_panel.py、measurement_plots.py、models/viewer/layout/editor，以及conftest、对应测试和计划记录。界面当前可用Process/Measure、多ROI、卡片/分布/双向剖面；Calculate及输出接线待后续任务。Current frame帧号TextBox依主代理审查在任务12补齐。

## 阶段 III

- 任务 8：实现与验证完成，提交待办。红灯：measurement_store缺模块（1 collection error）。首轮6 passed in0.46s；元数据损坏扩展先见2 failed（已显式关闭测试SQLite句柄，排除fixture句柄错误），实现key_json/row_count校验后8 passed in0.71s。增加(local_frame,roi_id)索引，避免完整CSV迭代每次重新全表排序。
- 任务 9：实现与验证完成，提交待办。红灯：measurement_job缺模块（1 collection error）。首轮job/store/stats58 passed in1.54s；补清理异常所有权和SQLite完成期间取消先见2 failed，再实现AnalysisOutcome.cleanup_store资源出口及发布前再次检查cancel，目标集60 passed in1.40s，父代理独立复测60 passed in1.33s。cleanup_store是待清理资源而非有效结果，UI交session.release_measurement_store重试。
- 任务 10：实现与聚焦验证完成，提交待办。红灯：Calculate缺按钮（1 failed）、draw_frame_curves缺函数（1 collection error）。首轮workflow/plots8 passed in27.24s。补重复poll和重算保留旧图先见2 failed，再清空已消费job/request并先将panel置busy防重绘；workflow/plots/pages15 passed in51.57s。包括实际Cancel恢复同key旧store、单帧slider保持禁用、native export互斥、start失败、空outcome、关闭cancel/join/cleanup。补普通文字图例先见mathtext解析失败后关闭legend parse_math。双ROI曲线实际PNG1 passed in5.80s；稳定图在picture/process-measure-review-1792613669127/measure-complete-curves.png，父代理已查看通过。

阶段 III 完整unit结果：1 failed、597 passed、2 skipped in418.24s。唯一失败为旧launch_viewer保活测试替身ViewerToken缺少新增shutdown_analysis协议；补充替身方法且新增wait=True断言后该测试1 passed in0.29s，原保活断言保留。不是生产逻辑失败；最终全部代码会重新完整回归，不将该首轮记为全绿。

## 阶段 IV

- 任务 11：实现与验证完成，提交待办。红灯：measurement_export缺模块（1 collection error）。首轮39 passed、2 skipped in1.15s；补2503帧×2 ROI完整CSV、错误key/未complete、source硬链接、Unicode/公式、快照数组修改和长标题。冻结数组及长标题先见红，再从冻结矩阵重算只读分析数组和标题换行。三种PNG均真实解码且已查看，稳定图在picture/process-measure-review-1792613669127/report-{distribution,profile,curves}.png。PNG/CSV路径选择适配层新增测试先缺方法红灯，再复用主线程Tk生命周期。最终组合验证记录见后续补充。
- 任务 11 最终组合验证：export/plots/dialogs/file_safety **49 passed、2 skipped in2.05s**；父代理独立export模块26 passed in1.77s。
- 任务 12：实现完成，完整unit回归运行中，提交待办。红灯缺少Save setup按钮和current_frame_box，2 failed、16 deselected in5.47s。接线后发现当前分析生成前刷新输出可用性，首轮5 failed、3 passed，修正后workflow/dialogs34 passed in108.41s。补全未勾选禁用、读帧失败回归先见3 failed，再实现checked门与成功读帧后更新timeline，4 passed、16 deselected in15.99s。原Process保存不额外传scope，兼容既有替身。Measure保存保留目标/备份快照，导出在对话框前冻结并锁定。24种DPI×窗口×子视图真实renderer检查24 passed、13 deselected in66.82s；8张稳定PNG在picture/process-measure-review-1792613669127/measure-final-*.png，父代理实际查看并通过布局审查。
- 任务 13：真实最小SEQ连接验证及文档进行中。首跑1 failed in14.46s，已通过统计/worker/输出/5帧提取，末尾测试遗漏apply_selection_document所需path关键字（测试API错误，无生产修改）。修正后继续真实profile直接数值对照。README/CHANGELOG/KNOWN_ISSUES按中文技术记录规范更新，区分自动回归与待用户人工验收，不升级版本、不宣称大文件温度验收。

任务12阶段完整unit：**663 passed、2 skipped in584.93s**。该进程在末尾F1/native计时器修正前载入代码，不作为最终全量数字。末尾审查补真实KeyEvent F1（Add后、长名重命名后立即显示最新全文）和旧native timer不能解除analysis busy：先见2 failed，其中F1仍显示旧名；按键时只读取settings更新详情，poll_export对analysis/analysis_export早返回。最终聚焦3 passed、57 deselected in16.12s。长名fixture末尾空格按既有模型trim契约修正断言，不改变产品名称规则。

任务13目标真实集成组合：**7 passed in26.71s**；ROI改到样品可见区域后单独 **1 passed in13.55s**。两种profile与block有限值均值直接对照；worker对源10/12/14五种统计/有效数、五帧真实时间及完整10行CSV核对；v3重开、PNG解码、五帧配套子SEQ ROI恢复/结果为空均验证。稳定实图：picture/process-measure-review-1792613669127/real-sample-complete-curves.png，父代理及实现代理实际查看，布局通过；此图仅展示测试，不作实验结论。

最终冻结后运行 `python -m pytest -q -rs -p no:cacheprovider --tb=short`：**672 passed、2 skipped in605.42s，退出码0**。两项skip仅为WinError 1314无法创建文件/目录符号链接，未削弱原断言。全部生产代码与测试在此运行前冻结，结束后只更新文档/checkbox。所有git提交步骤仍未完成；无新提交，不绕过审批。

运行入口由父代理只读核验：`sys.executable=C:\Users\Tracer\AppData\Local\Programs\Python\Python313\python.exe`，`flir_seq_tool.__file__=D:\carbonfoam\infrared\src\flir_seq_tool\__init__.py`。因此README原CLI会读取当前本地实现，无需重装。README人工试用位于第113行起，启动命令第118行，Confirm/Apply第121行，Save setup及Process Load区别第124行。

最终全量进程外raw前后快照按完整JSON逐字符比较为相同（文件名、长度、UTC mtime ticks），集成fixture另以finally检查纳秒mtime：

| 原始文件 | 前后相同长度（bytes） | 前后相同mtime（UTC Ticks） |
| --- | ---: | ---: |
| Rec-000366-240_09_26_37_152.seq | 42873727 | 639235060000000000 |
| Rec-000367-240_09_26_49_992.seq | 1518245059 | 639235060640000000 |
| Rec-000368-240_09_30_50_732.seq | 7625825755 | 639235065440000000 |

## 阶段IV交付与待办

任务1–13的实现及自动验证完成；git add/commit因审批等待按父代理指示暂缓，所有提交步骤保留未勾选。分支`codex/process-measure`，HEAD仍`bd4e8994ad302f6864b66cece3499feb5b9512c0`。`git diff --check`退出码0，仅提示计划文件未来Git操作可能LF转CRLF。未push/merge/tag/release；未安装升级依赖，raw未修改。

待提交范围（全部位于本目录）：

- 新增生产模块：measurement_models/stats/state/store/job/panel/plots/export、pixel_geometry、viewer_pages。
- 修改生产模块：selection、export_bundle、viewer、viewer_layout、selection_dialogs、transform_editor；`.gitignore`仅新增analysis/。
- 新增测试：unit/conftest、test_measurement_*、test_pixel_geometry、test_viewer_pages；integration/test_small_seq_measure。
- 修改测试：test_selection、test_export_bundle、test_viewer、test_selection_dialogs。旧BUG-005等真实鼠标事件断言保留。
- 文档：README、CHANGELOG、docs/KNOWN_ISSUES、本计划与执行记录。

计划中的细小接口修正已在各任务注明：clear交还资源所有权、cleanup_store独立错误资源出口、CheckButtons不进入通用set_active生命周期、冻结分析数组重算/只读、成功读帧后才移动timeline、F1按键即时更新ROI全文、旧native计时器在analysis期间早返回。没有扩大功能范围或引入新依赖。

剩余边界：Windows原生窗口/对话框和两份大SEQ性能仍待用户按README第113行起人工验收，不标通过。最终交付使用requesting-code-review流程交回持续审查的父代理；遵守唯一实现代理约束，不另派review代理。分支按用户要求原样保留，不请求合并/推送。
