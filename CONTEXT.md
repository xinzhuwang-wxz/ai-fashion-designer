# AI Fashion Designer

一款让设计师/电商卖家从一张灵感图出发、经 AI 抠图→发散→线稿→试布→实时编辑→高清成品的辅助设计工具。本文件是该领域的术语表（ubiquitous language），只定义"是什么"，不含实现细节。

## Language

### 资产与谱系

**DesignProject（设计项目）**:
一次完整设计会话的根容器；从灵感图到最终成品的所有资产都挂在它之下。
_Avoid_: session（旧代码用 session 既指 WebSocket 连接又指项目，已废弃此义）、会话

**DesignAsset（设计资产）**:
设计流程中任一可追溯的图像产物，拥有稳定身份、所属类型（kind）与父资产。
_Avoid_: image、图片、base64 字段

**Lineage（谱系）**:
资产间的父子链，记录"由哪个资产派生而来"，是整个产品可追溯性的脊柱。
_Avoid_: history、版本树

**GenerationJob（生成作业）**:
一次 AI 推理的运行记录（目标资产、所用模型、seed、状态、耗时）。
_Avoid_: task、request、调用

### 资产类型（DesignAsset 的 kind）

**Upload（上传原图）**:
用户上传的原始服装照片，作为谱系的根资产；抠图链路由它派生 Cutout。
_Avoid_: 原图、source image

**Cutout（抠图）**:
去除背景后只剩干净服装轮廓的资产。是**内部中间产物 / 渲染的源**，不是右画布要展示给用户的"成衣"。
_Avoid_: removed_bg、分割图、（不要把它当右画布的成品）

**Rendered Garment（成衣渲染图）**:
右画布展示的主体——基于抠图/线稿渲染出的、**介于"线描"与"照片"之间、棚拍质感的成衣图**（如那件淡紫大衣的调性）。由 Variation / Material / Final 等渲染类资产充当；**绝不是 Cutout 本身**。
_Avoid_: 实物图、照片、抠图

**Variation（变体）**:
由 Cutout 发散出的设计方案候选，保持比例结构、只在设计细节上变化。
_Avoid_: 缩略图、方案图

**Selected Variation（选中变体）**:
用户从 Variation 集合中挑定、并作为后续 Lineart 唯一来源的那一个变体。是修复"选了变体却没生效"链路的关键概念。
_Avoid_: current、preview（前端的预览 URL 不等于已选中）

**Lineart（线稿）**:
只保留结构线条、去除光影与颜色的设计图。
_Avoid_: 草图（草图特指用户手绘输入，见下）、edge

**Material（材质方案）**:
在 Lineart 之上叠加 面料 / 颜色 / 图案 三类参数后渲染出的成衣效果图。
_Avoid_: fill、填充图、布料图

**Edit Version（编辑版本）**:
用户以笔触表达局部修改意图、经局部重绘后产生的资产。
_Avoid_: 重绘图

**Final（成品）**:
可直接用于生产或电商展示的高清最终交付图。
_Avoid_: output、结果图

### 交互形态

**Dual-canvas Workbench（双画布工作台）**:
并排工作界面。**左 = 可编辑线稿**（自动提取或手绘皆可；提取后直接出现在左侧、形态像手画、可任意修改），**右 = Rendered Garment（成衣渲染图）**，非 Cutout。改左侧线稿 → 右侧跟着重渲（实时见 [[realtime-edit-masked-local-inpaint]]，ADR-0003）。是本产品区别于"上传后依次点按钮"的核心体感。
_Avoid_: 编辑器、画板（单画布语义）；右画布≠抠图

**Sketch-first Entry（草图优先入口）**:
不上传照片、直接在左画布手绘或上传线稿作为设计起点的入口；与上传照片入口在 Lineart 处汇合。
_Avoid_: 手绘模式、画图模式
