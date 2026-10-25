# 为实验配置设备 {#set-up-devices-for-an-experiment}

打开**设备与驱动**可添加、命名、测试或停用设备，
无需先创建实验配置。选中设备只读取详情；**连接** 打开手动会话，
**测试连接** 则执行单独的连接测试。实验与手动会话遵守同一套设备占用规则。

三类修改各有入口：

- **设备与驱动**：地址、传输选项、已安装驱动和设备安全要求。
- **参数与绑定 → 实验 setup**：实验使用的设备别名及其绑定的已登记设备。
- **工作参数与已保存版本**：科学输入、估计值和已保存的参数分支。

实验配置（setup）是带名称的设备、角色、通道和执行策略定义。
它引用已登记设备，不另存一份可独立修改的连接信息。
每个实验页或 Python 会话都可以选择自己的实验配置。

## 从模板开始 {#start-from-a-template}

在**参数与绑定 → 实验 setup → 配置模板**中选择可用模板，点击 **导入配置模板**。
应用保存实验配置和独立的参数版本。
选择 **将导入配置用于下次实验**，再选择样品并填写实验输入，
无需全局激活。

模板可以复用已登记设备，但不能覆盖其连接设置。若导入提示连接不同，
请先检查已登记设备；重复导入不会修改设备地址。

## 复用或修改实验配置 {#reuse-or-revise-a-setup}

在**参数与绑定 → 实验 setup** 中选择已保存的实验配置，
调整设备绑定，在 **另存为** 中填写新名称，再点击 **保存 setup**，原定义仍可使用。
设备安全要求适用于所有实验配置，不能通过实验的成功或失败策略放宽。

拓扑、路由等由作者编写的配置需要在 Python 中修改：

```python
definition = lab.setup.definition("bench-a").definition
revised = definition.model_copy(update={"routing": reviewed_routing})
setup = lab.setup.save(revised, name="bench-a-rewired", note="Reviewed wiring")
```

`reviewed_routing` 是实验或适配器作者提供的路由。保存实验配置不会打开设备，也不会改变其他页面。

准备实验前，解析具名配置，并将解析得到的精确配置与参数一起选入会话：

```python
setup = session.setup.get("bench-a-rewired")
session.use(parameter_branch="daily", setup=setup)
prepared = session.prepare(experiment, parameters=session.params)
```

`setup.get(name)` 根据当前已登记的设备连接解析定义，结果保留精确的连接修订和驱动身份。
如果提交前相关设备发生变化，请重新检查配置并预览。
已提交的工作保留提交时捕获的输入，不会自动追随后来的连接版本。

## 修改或停用设备 {#change-or-retire-a-device}

修改连接前，先完成或取消受影响的排队实验，并关闭手动会话。
应用检查设备占用，在提交修改前释放空闲连接；释放失败时保留旧连接修订，
并报告需要恢复的会话。

重命名只改变显示名称。停用设备会保留记录，并阻止新工作解析到该设备。
停用成功后，其地址可以分配给替换设备；旧实验快照仍引用原来已停用的设备。

不使用实验配置、直接通过 Python 控制设备时，参见[控制仪器](control-instruments.md)。
编辑参数请参见[参数分支](parameter-branches.md)。
