# Sony A7R II 实机验收清单

> 本清单供人工执行。**不要**在未经确认的情况下自动安装 APK 或用 ADB
> 修改相机系统设置。每一项记录实际现象与证据。

## 实测结果（2026-09-22，kanzaki-chiya）

- ✅ **RAW+JPEG 实机验证通过**：「RAW与JPEG」模式实拍一张，
  存储卡同时生成 **`.ARW` 41.1 MB** + **`.JPG` 10.3 MB**，序号一致。
  ARW 体量符合 A7R II 全画幅无压缩/压缩 RAW 预期（~40MB 级），JPG 为正常
  胶片风格输出。
- APK：`FilmStudio-0.2.2-alpha-movie.apk`（17 滤镜，自签
  `CN=SonyPMCADebug`），经 pmca-gui 安装（先卸载同包名旧版）。
- 完整逐项（滤镜切换稳定性、纯 RAW 模式、模式互斥、异常恢复）仍在
  下方清单中，建议按序补测；核心 RAW 落盘能力已证实。

## 0. 前置准备

- [ ] 备份机身设置（菜单 → 设置 → 保存/加载设置，或记录关键项）。
- [ ] 使用一张**已格式化、可清空**的 SD 卡；保留卡内原有素材的备份。
- [ ] 记录当前固件版本（菜单 → 设置 → 版本）与已装 Picture Effect+ 版本。
- [ ] 准备 `adb` 可用环境（Wi-Fi ADB 或 USB）以便抓 log，但**不**用于改设置。
- [ ] 充满电池；RAW 连拍耗电更高。

## 1. 安装与启动

- [ ] 用 pmca-gui 安装 `FilmStudio-*.apk`（参照 `docs/INSTALL.zh-CN.md`）。
- [ ] 启动应用，确认胶片滤镜菜单正常列出（17 项）且取景器色彩正常。
- [ ] 拍摄一张 JPEG，确认胶片风格生效（对照已知色彩）。

## 2. RAW + JPEG 模式

- [ ] 进入画质菜单，确认出现「RAW与JPEG」与「RAW」两项且可选中。
- [ ] 选「RAW与JPEG」，在默认滤镜下拍一张。
- [ ] 检查存储卡：应同时存在 `DSC*.JPG` 与 `DSC*.ARW`，文件序号一致。
- [ ] 用 `exiftool DSC*.ARW` 确认：`File Type` 为 ARW、`Bits Per Sample`
      合理（预期 14bit 压缩或 12/14bit）、`Image Width/Height` 为
      7976×5320 或机身原生 RAW 尺寸、含 `Sony` Make/Model。
- [ ] 用 RAW 解码器（ Lightroom / darktable / RawTherapee / `dcraw` ）打开
      ARW，确认可解码、画面与 JPG 构图一致。
- [ ] 对比 JPG 与 ARW 转出的 TIFF：JPG 应带胶片色调，ARW 为原始色彩。

## 3. 纯 RAW 模式

- [ ] 选「RAW」，拍一张：应只产生 `DSC*.ARW`，无同名 JPG。
- [ ] 回放/索引确认机身不报"无图像"。

## 4. 滤镜 × RAW 组合

- [ ] 在「RAW与JPEG」下依次切换至少 3 个不同滤镜（如富士 Velvia、理光
      正片、徕卡经典），各拍一张，确认 JPG 色调随之变化、ARW 始终原始。
- [ ] 切换滤镜后返回画质菜单，确认「RAW与JPEG」仍保持选中（未被重置）。

## 5. 模式与状态

- [ ] 拍照↔录像模式切换：RAW 选项在录像模式下应被隐藏或拒绝（原生互斥）；
      记录实际表现。
- [ ] 录像模式下若选 RAW，拍摄应失败或被忽略 —— 记录是否报错。
- [ ] 连拍（连拍 Hi）下 RAW+JPEG：确认 ARW 连续写入、无丢帧异常。
- [ ] 拍摄中途断电/拔卡/强制退出 App，重启后检查取景器色彩是否恢复正常
      （不残留异常 Gamma/Matrix）。

## 6. 退出与恢复

- [ ] 退出 App，进入系统相册/回放，确认无色彩残留。
- [ ] 重新进入 App，确认画质选项保持上次选择（或回退到安全默认 JPEG）。
- [ ] 卸载测试 APK 后，确认原生 Picture Effect+ 功能不受影响。

## 7. 文件取证

- [ ] `exiftool -G -s DSC*.ARW` 记录关键字段：BitsPerSample、Compression、
      CFA pattern、黑/白电平。
- [ ] 记录 `DSC*.JPG` 的 `Software`/`Processing` 字段，确认经胶片管线。
- [ ] 若 ARW 无法解码或大小异常（≈0、或 ≈JPG 大小），拍照留证并回退。

## 判定标准

| 项 | 通过条件 |
|---|---|
| RAW+JPEG | 同序号 JPG+ARW 同时生成；JPG 带滤镜色；ARW 可解码、为原始数据 |
| 纯 RAW | 仅 ARW 生成，可解码 |
| 菜单稳定性 | 切换滤镜/模式后画质选择不被意外重置 |
| 无回归 | 原有 JPEG 胶片拍摄、滤镜切换、取景器预览完全正常 |
| 恢复 | 退出/异常后无异常色彩参数残留 |
