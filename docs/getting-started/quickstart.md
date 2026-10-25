# 第一次实验 {#first-experiment}

按[下载与安装](install.md)安装并打开 Scopecat，从**帮助（Help）→
Learn with Notebooks → Parameters and scans（参数与扫描）**开始。
课程使用合成数据，不需要设备。若只想查看收到的数据文件，请转到
[打开与分享已有数据](../how-to/open-and-share-data.md)。

1. 选中 **Parameters and scans**，点击 **Start parameters Notebook**。
   帮助页会创建可编辑的作者目录并准备 Python；首次准备需要联网下载 Python
   和第三方依赖。开始前可用 **Choose another save location…** 更换保存位置，
   无需另外登记源码。准备中请等待；失败后 **Retry preparation** 会在同一目录继续并保留编辑。
2. 在装有 Python 和 Jupyter 扩展的 VS Code 中打开显示的
   `notebooks/parameters.ipynb`，选择显示的 `.venv` 作为 Notebook 内核。
   编辑器无法自动打开时，帮助页仍会显示目录、Notebook 和 Python 路径。
3. 按单元格依次连接应用、打开课程参数分支、预览七点扫描，再执行采集单元格，
   每点采集 64 次（shots）。独立的结果单元格读取这次已保存的运行，并链接到
   **运行详情（Runs）**；重复执行结果单元格不会重新采集。
4. 按[课程编辑步骤](../tutorials/teaching-sandboxes.md#learn-with-notebooks)
   修改 `src/my_experiment/teaching.py` 中的 shots，重新构造请求并采集一次新运行。
   旧结果仍保留原始源码与输入。
5. 保存编辑后，用 **Continue parameters Notebook** 回到同一课程。
   帮助页的 **View this course in Data** 可进入本课程的数据；没有课程归属时显示
   **Find saved results in Data**。在**运行详情（Runs）**中找到目标运行并复制准确的运行 ID。
   内核重启后，先重新连接并打开课程参数，再用 `run = session.run("准确的运行 ID")`
   选中已保存的运行，执行结果单元格。不要用 **Run All** 回找结果；它会开始新工作。
   等待超时应继续等待同一个作业，不要重复提交采集。

可以直接在课程目录继续编写实验：它已登记到应用，无需复制或再次登记。
若要新建独立作者目录，使用**设置（Settings）→ Author code → New code folder**，
再按[起步实验指南](../tutorials/starter-authoring.md)操作；该示例使用不同的文件。

额外的分析包应安装到你选择的 Python，不要安装到应用内置 Python。
准备失败或接入现有实验室代码时，参见
[维护应用：作者目录](../how-to/maintain-application.md#author-folders)。
关闭最后一个窗口会将应用留在托盘或菜单栏；选择 **Quit** 才退出。
重新打开 Scopecat 后，可从**运行详情（Runs）**查看同一份已保存结果，不会再次测量。

下一步可选[其他 Notebook 课程](../tutorials/teaching-sandboxes.md#choose-a-notebook-route)，
或学习[跨会话继续实验](../how-to/continue-experiment.md)。
框架贡献者请使用[日常源码开发](../development/public-preview.md#daily-source-desktop)流程。
