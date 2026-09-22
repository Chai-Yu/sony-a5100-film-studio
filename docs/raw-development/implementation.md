# RAW 功能实现说明

## 功能入口

应用「画质」菜单（`setPictureStorageFormat`，位于相机菜单的画质设置层）
新增两个选项：

- **RAW与JPEG**（`Value="rawjpeg"`）：一次快门同时写 `DSC*.JPG` 与
  `DSC*.ARW`；JPEG 走当前选中的胶片模拟（RGB Matrix + Extended Gamma）。
- **RAW**（`Value="raw"`）：只写 `DSC*.ARW`，不经胶片管线。

原 JPEG 细分项（如 `fine`/`standard`，由 Ricoh v1.1.4 底座决定）保留不变。

## 数据流

```
用户选择「RAW与JPEG」
   └─ MenuData: ItemId=setPictureStorageFormat_rawjpeg, Value=rawjpeg
   └─ PictureQualityController.setValue → CameraSetting.setPictureStorageFormat("rawjpeg")
   └─ Sony CameraEx 原生路径
   └─ 快门 → DSC0001.JPG（含胶片风格）+ DSC0001.ARW（原始传感器数据）
```

菜单项的出现由 `getAvailableValue` 控制；本补丁在该处把
`AvailableInfo.isAvailable` 对 `rawjpeg`/`raw` 的否定答案改写为肯定，
使菜单项可见可选。值一旦被选中，后续链路完全走 Sony 原生代码，没有
自定义封装。

## 关键实现点

### `filterQualityAvailability`

```smali
.method public static filterQualityAvailability(Ljava/lang/String;Z)Z
```

- `p0`：候选画质值（`rawjpeg`/`raw`/`jpeg`/…）
- `p1`：`AvailableInfo.isAvailable` 的原生答案
- 行为：`p1==false` 时返回 `p1`（原样）；`p0` 是 RAW 值且原生答案为 true
  时返回 true；其余返回 `p1`。**注意**：它只在原生已经放行时才放行 RAW，
  并不把原生拒绝变成接受 —— 上游语义即如此，因为 `isAvailable` 对 RAW
  的真实回答是 false（隐藏）而非"机身不支持"。

### 菜单项属性

两个 `Layer2` 带 `CautionID`（对应 Sony 的 RAW 相关警告组）、`ConfigClass`
指向 `PictureQualityController`、`ExecType="SET_VALUE"`、图标引用基础包
已有的 `uncompressed_raw` 素材。

### 显示名

`MenuData.xml` 的 `Title`/`DisplayName` 写中文作静态 fallback；
`RicohHook.getFilterName`/`getFilterGuide` 对两个 ItemId 返回
`RAW与JPEG`/`RAW` 及对应 guide，与滤镜项同一查表路径。

## 与胶片模拟的关系

- 互不影响：画质选择是 `CameraSetting` 的独立参数，胶片钩子写
  `ParametersModifier` 的 RGB Matrix/Gamma，两者在 ISP 内正交。
- RAW+JPEG 模式下 JPEG 为胶片风格直出、ARW 为原始数据 —— 符合
  Sony 原生 RAW+JPEG 语义。
- 纯 RAW 模式下不产生 JPEG，胶片钩子对取景器仍有影响（取景器预览），
  但不写入文件。

## 已知限制（待实机验证）

- 上游未处理：切换滤镜时画质选项是否被重置为 JPEG；录像模式下纯 RAW
  是否被原生互斥拒绝；`filterQualityAvailability` 是否在其他路径
  （如快门执行前的二次校验）被绕过。
- 图标使用 `uncompressed_raw` 素材，若输入的 Ricoh v1.1.4 底座不含该
  drawable，菜单项将无图标显示（不影响功能）。
