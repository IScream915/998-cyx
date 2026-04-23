from __future__ import annotations

from pathlib import Path

import torch
from torch import Tensor, nn

from training.zone_seg_head import ZoneSegHead


class ConvBlock(nn.Module):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        stride: int = 1,
        kernel_size: int = 3,
    ):
        super().__init__()
        padding = kernel_size // 2
        self.block = nn.Sequential(
            nn.Conv2d(
                in_channels,
                out_channels,
                kernel_size,
                stride=stride,
                padding=padding,
                bias=False,
            ),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x: Tensor) -> Tensor:
        return self.block(x)


class ResidualBlock(nn.Module):
    def __init__(self, channels: int):
        super().__init__()
        self.block = nn.Sequential(
            ConvBlock(channels, channels, kernel_size=3),
            ConvBlock(channels, channels, kernel_size=3),
        )

    def forward(self, x: Tensor) -> Tensor:
        return x + self.block(x)


class DecoupledDetectHead(nn.Module):
    def __init__(
        self,
        in_channels: int,
        num_classes: int,
        box_format: str = "legacy_cxcywh",
        reg_max: int = 0,
    ):
        super().__init__()
        self.box_format = str(box_format)
        self.reg_max = int(reg_max)
        if self.box_format == "ltrb_dfl" and self.reg_max < 1:
            raise ValueError("ltrb_dfl requires reg_max >= 1")
        hidden_channels = 128
        self.stem = nn.Sequential(
            ConvBlock(in_channels + 2, hidden_channels, kernel_size=1),
            ResidualBlock(hidden_channels),
        )
        self.box_tower = nn.Sequential(
            ConvBlock(hidden_channels, hidden_channels, kernel_size=3),
            ConvBlock(hidden_channels, hidden_channels, kernel_size=3),
        )
        self.obj_tower = nn.Sequential(
            ConvBlock(hidden_channels, hidden_channels // 2, kernel_size=3),
            ConvBlock(hidden_channels // 2, hidden_channels // 2, kernel_size=3),
        )
        self.cls_tower = nn.Sequential(
            ConvBlock(hidden_channels, hidden_channels, kernel_size=3),
            ConvBlock(hidden_channels, hidden_channels, kernel_size=3),
        )
        box_channels = 4 if self.box_format == "legacy_cxcywh" else 4 * (self.reg_max + 1)
        self.box_head = nn.Conv2d(hidden_channels, box_channels, kernel_size=1)
        self.obj_head = nn.Conv2d(hidden_channels // 2, 1, kernel_size=1)
        self.cls_head = nn.Conv2d(hidden_channels, num_classes, kernel_size=1)
        nn.init.constant_(self.obj_head.bias, -3.0)
        nn.init.constant_(self.cls_head.bias, -2.0)

    def forward(self, x: Tensor) -> Tensor:
        x = self._append_coords(x)
        shared = self.stem(x)
        box = self.box_head(self.box_tower(shared))
        obj = self.obj_head(self.obj_tower(shared))
        cls = self.cls_head(self.cls_tower(shared))
        return torch.cat((obj, box, cls), dim=1)

    @staticmethod
    def _append_coords(x: Tensor) -> Tensor:
        batch, _, height, width = x.shape
        yy, xx = torch.meshgrid(
            torch.linspace(-1.0, 1.0, height, device=x.device, dtype=x.dtype),
            torch.linspace(-1.0, 1.0, width, device=x.device, dtype=x.dtype),
            indexing="ij",
        )
        coords = torch.stack((xx, yy), dim=0).unsqueeze(0).expand(batch, -1, -1, -1)
        return torch.cat((x, coords), dim=1)


class MultitaskYOLO(nn.Module):
    """
    Lightweight multitask scaffold for first-stage training smoke tests.

    The model shares a simple CNN backbone between a grid-based detection head and
    the blind-spot segmentation head. It is intentionally smaller than the final
    YOLOv8 integration so the project can validate data and training wiring now.
    """

    def __init__(
        self,
        num_classes: int = 6,
        input_size: int = 640,
        box_format: str = "legacy_cxcywh",
        reg_max: int = 0,
        pretrained_path: str | None = None,
    ):
        super().__init__()
        self.num_classes = num_classes
        self.box_format = str(box_format)
        self.reg_max = int(reg_max)
        if self.box_format == "ltrb_dfl" and self.reg_max < 1:
            raise ValueError("ltrb_dfl requires reg_max >= 1")
        self.stem = ConvBlock(3, 32, stride=2)
        self.shallow_stage = nn.Sequential(
            ConvBlock(32, 64, stride=2),
            ResidualBlock(64),
        )
        self.deep_stage = nn.Sequential(
            ConvBlock(64, 96, stride=2),
            ResidualBlock(96),
        )
        self.context_stage = nn.Sequential(
            ConvBlock(96, 128, stride=1),
            ResidualBlock(128),
            ResidualBlock(128),
        )
        self.skip_downsample = ConvBlock(64, 64, stride=2)
        self.neck = nn.Sequential(
            ConvBlock(64 + 128, 128, kernel_size=1),
            ResidualBlock(128),
            ResidualBlock(128),
        )
        self.det_head = DecoupledDetectHead(
            128,
            num_classes,
            box_format=self.box_format,
            reg_max=self.reg_max,
        )
        self.seg_head = ZoneSegHead(128, out_h=input_size, out_w=input_size)

        if pretrained_path:
            weight_path = Path(pretrained_path)
            if weight_path.exists():
                checkpoint = torch.load(weight_path, map_location="cpu", weights_only=False)
                state_dict = checkpoint.get("model", checkpoint) if isinstance(checkpoint, dict) else checkpoint
                if hasattr(state_dict, "state_dict"):
                    state_dict = state_dict.state_dict()
                self._load_compatible_state_dict(state_dict)

    def forward(self, x: Tensor) -> dict[str, Tensor]:
        stem = self.stem(x)
        shallow = self.shallow_stage(stem)
        deep = self.deep_stage(shallow)
        context = self.context_stage(deep)
        skip = self.skip_downsample(shallow)
        features = self.neck(torch.cat((skip, context), dim=1))
        det = self.det_head(features)
        seg = self.seg_head(features)
        return {"det": det, "seg": seg}

    def _load_compatible_state_dict(self, state_dict: dict[str, Tensor]) -> None:
        current_state = self.state_dict()
        compatible = {
            key: value
            for key, value in state_dict.items()
            if key in current_state and current_state[key].shape == value.shape
        }
        self.load_state_dict(compatible, strict=False)
