"""Template catalog and safe storage rules for JetKVM visual templates."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple, Union


@dataclass(frozen=True)
class TemplateSpec:
    """One operator-selectable template role."""

    key: str
    label: str


_COMMON_SPECS = (
    TemplateSpec("window", "視窗定位"),
    TemplateSpec("input", "輸入欄位"),
    TemplateSpec("button", "確認按鈕"),
    TemplateSpec("testing", "測試中"),
    TemplateSpec("pass", "通過結果"),
    TemplateSpec("fail", "失敗結果"),
)

_DEVICE_SPECS: Dict[str, Tuple[TemplateSpec, ...]] = {
    "DFU": _COMMON_SPECS
    + (
        TemplateSpec("slot_label", "Slot 標籤"),
        TemplateSpec("group_label", "群組標籤"),
        TemplateSpec("checkbox_checked", "已勾選核取方塊"),
        TemplateSpec("checkbox_unchecked", "未勾選核取方塊"),
        TemplateSpec("dock_icon", "Dock 圖示"),
    ),
    "FCT": (
        TemplateSpec("window", "視窗定位"),
        TemplateSpec("testing", "測試中"),
        TemplateSpec("pass", "通過結果"),
        TemplateSpec("fail", "失敗結果"),
        TemplateSpec("dock_icon", "Dock Icon"),
    ),
    "BT": (
        TemplateSpec("window", "視窗定位"),
        TemplateSpec("testing", "測試中"),
        TemplateSpec("pass", "通過結果"),
        TemplateSpec("fail", "失敗結果"),
        TemplateSpec("start_all", "Start All"),
        TemplateSpec("dock_icon", "Dock Icon"),
    ),
}


class TemplateCatalog:
    """The sole authority for JetKVM template names, locations, and saving."""

    def __init__(self, root: Optional[Union[str, Path]] = None):
        self.root = Path(root).expanduser() if root is not None else self.default_root()

    @staticmethod
    def default_root() -> Path:
        return Path.home() / "Documents" / "template"

    @staticmethod
    def devices() -> Tuple[str, ...]:
        return tuple(_DEVICE_SPECS.keys())

    @staticmethod
    def normalize_device(device: str) -> str:
        normalized = str(device or "").strip().upper()
        if normalized not in _DEVICE_SPECS:
            raise ValueError("未知設備: {}".format(device))
        return normalized

    def specs(self, device: str) -> Sequence[TemplateSpec]:
        return _DEVICE_SPECS[self.normalize_device(device)]

    def keys(self, device: str) -> Tuple[str, ...]:
        return tuple(spec.key for spec in self.specs(device))

    def selection_options(self, device: str) -> Tuple[Tuple[str, str], ...]:
        """Return operator-facing template names paired with canonical keys."""
        return tuple(
            (spec.label if spec.key == "start_all" else spec.key, spec.key)
            for spec in self.specs(device)
        )

    def action_template(self, device: str, action: str) -> str:
        """Resolve a public operation name to the device's canonical template."""
        normalized_device = self.normalize_device(device)
        normalized_action = str(action or "").strip().lower()
        if normalized_device == "FCT":
            raise ValueError("{} 不支援 {} 操作".format(normalized_device, normalized_action))
        if normalized_device == "BT" and normalized_action == "input":
            raise ValueError("{} 不支援 {} 操作".format(normalized_device, normalized_action))
        if normalized_action != "button":
            raise ValueError("未知操作: {}".format(action))
        return "start_all" if normalized_device == "BT" else "button"

    def supports_action(self, device: str, action: str) -> bool:
        normalized_device = self.normalize_device(device)
        normalized_action = str(action or "").strip().lower()
        return (
            (normalized_device == "DFU" and normalized_action in ("input", "button"))
            or (normalized_device == "BT" and normalized_action == "button")
        )

    def focus_template(self, device: str) -> str:
        normalized_device = self.normalize_device(device)
        if normalized_device not in ("BT", "FCT"):
            raise ValueError("{} 不支援 Dock 前景化".format(normalized_device))
        return "dock_icon"

    def pre_action_template(self, device: str, action: str) -> Optional[str]:
        normalized_device = self.normalize_device(device)
        if normalized_device == "BT" and str(action or "").strip().lower() == "button":
            return "dock_icon"
        return None

    def validate_key(self, device: str, template_key: str) -> str:
        normalized_device = self.normalize_device(device)
        key = str(template_key or "").strip()
        if key not in tuple(spec.key for spec in _DEVICE_SPECS[normalized_device]):
            raise ValueError("設備 {} 不支援模板種類: {}".format(normalized_device, template_key))
        return key

    def device_dir(self, device: str) -> Path:
        return self.root / self.normalize_device(device)

    def filename(self, device: str, template_key: str) -> str:
        normalized_device = self.normalize_device(device)
        key = self.validate_key(normalized_device, template_key)
        return "{}_{}.png".format(normalized_device, key)

    def path(self, device: str, template_key: str) -> Path:
        normalized_device = self.normalize_device(device)
        return self.device_dir(normalized_device) / self.filename(normalized_device, template_key)

    def capture_path(self) -> Path:
        return self.root / "_captures" / "jetkvm_frame.png"

    def diagnostic_path(self, device: str, filename: str) -> Path:
        normalized_device = self.normalize_device(device)
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.endswith(".png"):
            raise ValueError("非法診斷圖片名稱: {}".format(filename))
        return self.root / "_captures" / normalized_device / safe_name

    def prepare_save(self, device: str, template_key: str, overwrite: bool = False) -> Path:
        target = self.path(device, template_key)
        if target.exists() and not overwrite:
            raise FileExistsError("模板已存在，未確認覆寫: {}".format(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def save_crop(
        self,
        image,
        box: Tuple[int, int, int, int],
        device: str,
        template_key: str,
        overwrite: bool = False,
    ) -> Path:
        """Validate and persist a cropped OpenCV image to its canonical path."""
        if image is None or not hasattr(image, "shape") or len(image.shape) < 2:
            raise ValueError("沒有可儲存的來源圖片")
        if len(box) != 4 or any(isinstance(value, bool) or not isinstance(value, int) for value in box):
            raise ValueError("非法裁切範圍")

        x1, y1, x2, y2 = box
        height, width = image.shape[:2]
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError("裁切範圍超出來源圖片或尺寸為零")
        if x2 - x1 < 5 or y2 - y1 < 5:
            raise ValueError("裁切範圍過小，至少需要 5 x 5 像素")

        target = self.prepare_save(device, template_key, overwrite=overwrite)
        crop = image[y1:y2, x1:x2].copy()
        import cv2

        if not cv2.imwrite(str(target), crop):
            raise OSError("無法寫入模板: {}".format(target))
        return target
