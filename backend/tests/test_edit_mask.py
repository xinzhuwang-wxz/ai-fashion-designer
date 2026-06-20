"""#7 局部编辑的坐标对齐核心（ADR-0003）：
前端传【图像归一化坐标】[0,1]，后端按图像像素尺寸生成 mask —— 任意画布
缩放/平移下 mask 都落在图上正确位置（屏幕坐标的转换在前端完成）。
"""
from app.services.mask_utils import normalized_strokes_to_mask


def test_mask_aligns_with_normalized_center_point():
    mask = normalized_strokes_to_mask(
        [{"x": 0.5, "y": 0.5}], img_w=100, img_h=200, brush_frac=0.1
    )
    assert mask.shape == (200, 100)  # (h, w)
    assert mask[100, 50] > 0  # 中心命中
    assert mask[0, 0] == 0  # 角落不命中


def test_mask_scales_with_image_dimensions():
    mask = normalized_strokes_to_mask(
        [{"x": 0.25, "y": 0.75}], img_w=400, img_h=400, brush_frac=0.05
    )
    assert mask[300, 100] > 0  # 0.75*400=300(y), 0.25*400=100(x)
    assert mask[100, 300] == 0  # 镜像位置不命中（验证 x/y 没搞反）


def test_empty_strokes_give_empty_mask():
    mask = normalized_strokes_to_mask([], img_w=100, img_h=100, brush_frac=0.1)
    assert int(mask.max()) == 0


def test_out_of_range_points_clamped_not_crash():
    mask = normalized_strokes_to_mask(
        [{"x": 1.5, "y": -0.2}], img_w=100, img_h=100, brush_frac=0.1
    )
    assert mask.shape == (100, 100)  # 不崩
