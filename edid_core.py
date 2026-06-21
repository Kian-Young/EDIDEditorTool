"""
EDID 核心模块 V2 - 支持 128/256/384 字节 EDID、CEA-861 扩展块、RTD 文件识别

EDID (Extended Display Identification Data) 结构:
  Block 0 (128B):  基础 EDID — 制造商、机种名、SN、详细时序、标准时序
  Block 1 (128B):  扩展块 1 — 通常为 CEA-861 (HDMI/DP 附加时序、音频)
  Block 2 (128B):  扩展块 2 — CEA-861 第二块 / DisplayID / Block Map
  ...

CEA-861 扩展块 (tag=0x02):
  Byte 0:    标签 0x02
  Byte 1:    版本号 (通常 3)
  Byte 2:    DTD 起始偏移
  Byte 3:    标志位 (underscan/audio/YCbCr/native DTD count)
  Bytes 4+:  Data Block Collection → 音频/视频/VSDB/HDR/...
  剩余空间:  附加 DTD (18 字节详细时序描述符)

RTD 文件:
  Realtek 显示器 Scaler 固件 (RTD2556/RTD2660/RTD27xx 等)
  EDID 通常嵌入在特定偏移处，也可通过搜索 EDID Header 定位
"""

import struct
import re
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Set
from enum import IntEnum

# ═══════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════
EDID_HEADER = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
EDID_BLOCK_SIZE = 128
MAX_BLOCKS = 3  # 384 字节 = 3 blocks
DESCRIPTOR_COUNT = 4
DESCRIPTOR_SIZE = 18

# CEA-861 扩展块标签
CEA_TAG = 0x02
DISPLAYID_TAG = 0x70
BLOCK_MAP_TAG = 0xF0
VTB_EXT_TAG = 0x10
LS_EXT_TAG = 0x50

# CEA Data Block 标签
CEA_DB_AUDIO = 1
CEA_DB_VIDEO = 2          # Short Video Descriptors
CEA_DB_VENDOR = 3         # HDMI VSDB etc.
CEA_DB_SPEAKER = 4
CEA_DB_VESA_DTC = 5
CEA_DB_EXTENDED = 7       # 扩展标签，实际类型在 byte+1

# CEA Extended Data Block 标签
CEA_EXT_VIDEO_CAP = 0
CEA_EXT_VENDOR_VIDEO = 1
CEA_EXT_COLORIMETRY = 5
CEA_EXT_HDR_STATIC = 6
CEA_EXT_HDR_DYNAMIC = 7
CEA_EXT_YCBCR420_VIDEO = 14
CEA_EXT_YCBCR420_CAPMAP = 15
CEA_EXT_VENDOR_AUDIO = 17

# HDMI IEEE OUI
HDMI_OUI = 0x000C03

# RTD 固件常见 EDID 偏移地址
RTD_KNOWN_OFFSETS = [0x00, 0x80, 0x100, 0x180, 0x200, 0x280, 0x300,
                     0x400, 0x800, 0x1000, 0x2000, 0x4000]


class DescriptorTag(IntEnum):
    """Monitor 描述符标签类型"""
    SERIAL_NUMBER = 0xFF
    UNSPECIFIED_TEXT = 0xFE
    MONITOR_RANGE = 0xFD
    MONITOR_NAME = 0xFC
    WHITE_POINT = 0xFB
    STANDARD_TIMING_IDS = 0xFA
    DUMMY = 0x10

    @classmethod
    def name_for(cls, tag: int) -> str:
        names = {
            0xFF: "序列号 (Serial Number)",
            0xFE: "未指定文本 (Unspecified Text)",
            0xFD: "显示器范围限制 (Monitor Range Limits)",
            0xFC: "机种名 (Monitor Name)",
            0xFB: "白点数据 (White Point Data)",
            0xFA: "标准时序标识 (Standard Timing IDs)",
            0x10: "哑元描述符 (Dummy)",
        }
        return names.get(tag, f"未知标签 (0x{tag:02X})")


# ═══════════════════════════════════════════════════════════════════════════
# 常用 VIC (Video Identification Code) 表
# ═══════════════════════════════════════════════════════════════════════════
VIC_TABLE: Dict[int, str] = {
    1:   "640×480p @ 60Hz",
    2:   "720×480p @ 60Hz",
    3:   "720×480p @ 60Hz (16:9)",
    4:   "1280×720p @ 60Hz",
    5:   "1920×1080i @ 60Hz",
    6:   "720(1440)×480i @ 60Hz",
    7:   "720(1440)×480i @ 60Hz (16:9)",
    10:  "2880×480i @ 60Hz (16:9)",
    12:  "2880×240p @ 60Hz (16:9)",
    14:  "1440×480p @ 60Hz",
    15:  "1440×480p @ 60Hz (16:9)",
    16:  "1920×1080p @ 60Hz",
    17:  "720×576p @ 50Hz",
    18:  "720×576p @ 50Hz (16:9)",
    19:  "1280×720p @ 50Hz",
    20:  "1920×1080i @ 50Hz",
    21:  "720(1440)×576i @ 50Hz",
    22:  "720(1440)×576i @ 50Hz (16:9)",
    31:  "1920×1080p @ 50Hz",
    32:  "1920×1080p @ 24Hz",
    33:  "1920×1080p @ 25Hz",
    34:  "1920×1080p @ 30Hz",
    39:  "1920×1080i @ 50Hz (16:9)",
    60:  "1920×1080p @ 120Hz",
    63:  "1920×1080p @ 120Hz",
    64:  "1920×1080p @ 100Hz",
    74:  "2560×1440p @ 60Hz",
    93:  "3840×2160p @ 24Hz",
    94:  "3840×2160p @ 25Hz",
    95:  "3840×2160p @ 30Hz",
    96:  "3840×2160p @ 50Hz",
    97:  "3840×2160p @ 60Hz",
    102: "4096×2160p @ 60Hz",
    106: "3840×2160p @ 120Hz",
    107: "3840×2160p @ 100Hz",
    114: "2560×1440p @ 120Hz",
    117: "3840×2160p @ 144Hz",
    118: "2560×1080p @ 60Hz",
}


# ═══════════════════════════════════════════════════════════════════════════
# Detailed Timing 描述符 (18 字节)
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class DetailedTiming:
    """详细时序描述符 (18 字节)

    各字段位宽:
      pixel_clock:   16 bit (单位 10kHz)
      h_active:      12 bit
      h_blanking:    12 bit
      v_active:      12 bit
      v_blanking:    12 bit
      h_front_porch: 10 bit
      h_sync:        10 bit
      v_front_porch:  6 bit
      v_sync:         6 bit
      h_image_size:  12 bit (mm)
      v_image_size:  12 bit (mm)
      h_border:       8 bit
      v_border:       8 bit
    """
    pixel_clock: int = 0
    h_active: int = 0
    h_blanking: int = 0
    v_active: int = 0
    v_blanking: int = 0
    h_front_porch: int = 0
    h_sync: int = 0
    v_front_porch: int = 0
    v_sync: int = 0
    h_image_size: int = 0
    v_image_size: int = 0
    h_border: int = 0
    v_border: int = 0
    interlaced: bool = False
    stereo_mode: int = 0
    sync_type: int = 3          # 3 = 数字分离同步
    sync_serration: bool = False
    sync_on_green: bool = False

    @property
    def h_total(self) -> int:
        return self.h_active + self.h_blanking

    @property
    def v_total(self) -> int:
        return self.v_active + self.v_blanking

    @property
    def refresh_rate(self) -> float:
        if self.h_total == 0 or self.v_total == 0:
            return 0.0
        return (self.pixel_clock * 10000) / (self.h_total * self.v_total)

    @property
    def pixel_clock_mhz(self) -> float:
        return self.pixel_clock / 100.0

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional['DetailedTiming']:
        if len(data) < 18:
            return None
        if data[0] == 0 and data[1] == 0:
            return None

        pc = data[0] | (data[1] << 8)
        ha = data[2] | ((data[4] & 0xF0) << 4)
        hb = data[3] | ((data[4] & 0x0F) << 8)
        va = data[5] | ((data[7] & 0xF0) << 4)
        vb = data[6] | ((data[7] & 0x0F) << 8)
        hfp = data[8] | ((data[11] >> 6) & 0x03) << 8
        hs = data[9] | ((data[11] >> 4) & 0x03) << 8
        vfp = (data[10] >> 4) | ((data[11] >> 2) & 0x03) << 4
        vs = (data[10] & 0x0F) | ((data[11] & 0x03) << 4)
        hsz = data[12] | ((data[14] & 0xF0) << 4)
        vsz = data[13] | ((data[14] & 0x0F) << 8)
        hbdr, vbdr = data[15], data[16]
        feat = data[17]

        return cls(
            pixel_clock=pc, h_active=ha, h_blanking=hb,
            v_active=va, v_blanking=vb,
            h_front_porch=hfp, h_sync=hs,
            v_front_porch=vfp, v_sync=vs,
            h_image_size=hsz, v_image_size=vsz,
            h_border=hbdr, v_border=vbdr,
            interlaced=bool(feat & 0x80),
            stereo_mode=(feat >> 5) & 0x03,
            sync_type=(feat >> 3) & 0x03,
            sync_serration=bool(feat & 0x04),
            sync_on_green=bool(feat & 0x02),
        )

    def to_bytes(self) -> bytes:
        buf = bytearray(18)
        buf[0:2] = self.pixel_clock.to_bytes(2, 'little')
        buf[2] = self.h_active & 0xFF
        buf[3] = self.h_blanking & 0xFF
        buf[4] = ((self.h_active >> 8) & 0x0F) << 4 | ((self.h_blanking >> 8) & 0x0F)
        buf[5] = self.v_active & 0xFF
        buf[6] = self.v_blanking & 0xFF
        buf[7] = ((self.v_active >> 8) & 0x0F) << 4 | ((self.v_blanking >> 8) & 0x0F)
        buf[8] = self.h_front_porch & 0xFF
        buf[9] = self.h_sync & 0xFF
        buf[10] = ((self.v_front_porch & 0x0F) << 4) | (self.v_sync & 0x0F)
        buf[11] = (((self.h_front_porch >> 8) & 0x03) << 6) | \
                  (((self.h_sync >> 8) & 0x03) << 4) | \
                  (((self.v_front_porch >> 4) & 0x03) << 2) | \
                  ((self.v_sync >> 4) & 0x03)
        buf[12] = self.h_image_size & 0xFF
        buf[13] = self.v_image_size & 0xFF
        buf[14] = ((self.h_image_size >> 8) & 0x0F) << 4 | ((self.v_image_size >> 8) & 0x0F)
        buf[15], buf[16] = self.h_border, self.v_border
        feat = 0
        if self.interlaced: feat |= 0x80
        feat |= (self.stereo_mode & 0x03) << 5
        feat |= (self.sync_type & 0x03) << 3
        if self.sync_serration: feat |= 0x04
        if self.sync_on_green: feat |= 0x02
        buf[17] = feat
        return bytes(buf)


# ═══════════════════════════════════════════════════════════════════════════
# Monitor 描述符 (非时序)
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class MonitorDescriptor:
    """显示器描述符：名称、序列号、范围限制等 (18 字节)

    字节布局:
      [0:2]   = 00 00 00 (时序标志位，为0表示是Monitor Descriptor)
      [3]     = 描述符标签 (0xFC=名称, 0xFF=序列号, 0xFD=范围, ...)
      [4]     = byte4 (EDID 1.4 中某些标签使用此字节，如 0xFD 范围描述符)
      [5:17]  = 13 字节有效载荷 (文本数据等)
    """
    tag: int = 0x10
    byte4: int = 0              # EDID 1.4 的扩展字段，必须保留原始值
    data: bytes = field(default_factory=lambda: bytes(13))

    @classmethod
    def from_bytes(cls, data: bytes) -> 'MonitorDescriptor':
        """解析时必须保留 byte4，不同标签有不同含义"""
        return cls(tag=data[3], byte4=data[4], data=bytes(data[5:18]))

    def to_bytes(self) -> bytes:
        """序列化时保留 byte4 的原始值"""
        buf = bytearray(18)
        buf[3] = self.tag
        buf[4] = self.byte4          # 保留原始 byte4，不再强制为 0
        buf[5:18] = self.data[:13].ljust(13, b'\x00')
        return bytes(buf)

    def get_text(self) -> str:
        text = self.data.decode('ascii', errors='replace')
        return text.rstrip('\n\r\x00\t ')

    def set_text(self, text: str):
        if len(text) > 13:
            text = text[:13]
        if len(text) < 13:
            text = text + '\n'
        self.data = text.encode('ascii', errors='replace').ljust(13, b' ')[:13]


# ═══════════════════════════════════════════════════════════════════════════
# 描述符块
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class DescriptorBlock:
    """EDID 中 1/4 个 18 字节描述符块"""
    index: int = 0
    is_timing: bool = False
    timing: Optional[DetailedTiming] = None
    monitor: Optional[MonitorDescriptor] = None
    raw: bytes = field(default_factory=lambda: bytes(18))

    @classmethod
    def from_bytes(cls, data: bytes, index: int) -> 'DescriptorBlock':
        block = cls(index=index, raw=data)
        if data[0] != 0 or data[1] != 0:
            block.is_timing = True
            block.timing = DetailedTiming.from_bytes(data)
            if block.timing is None:
                block.is_timing = False
        else:
            block.monitor = MonitorDescriptor.from_bytes(data)
        return block

    def to_bytes(self) -> bytes:
        if self.is_timing and self.timing:
            return self.timing.to_bytes()
        if self.monitor:
            return self.monitor.to_bytes()
        return self.raw

    @property
    def type_name(self) -> str:
        if self.is_timing and self.timing:
            return "详细时序 (Detailed Timing)"
        if self.monitor:
            return DescriptorTag.name_for(self.monitor.tag)
        return "空 / 未使用"

    @property
    def summary(self) -> str:
        if self.is_timing and self.timing:
            t = self.timing
            interlace = "i" if t.interlaced else "p"
            return f"{t.h_active}×{t.v_active}{interlace} @ {t.refresh_rate:.1f}Hz ({t.pixel_clock_mhz:.1f}MHz)"
        if self.monitor:
            tag, text = self.monitor.tag, self.monitor.get_text()
            tag_labels = {0xFC: '机种名', 0xFF: '序列号', 0xFD: '范围限制', 0xFE: '未指定文本'}
            label = tag_labels.get(tag, f"0x{tag:02X}")
            return f'{label}: "{text}"' if text else f"({label})"
        return "(空)"

    def set_as_dummy(self):
        self.is_timing = False
        self.timing = None
        self.monitor = MonitorDescriptor(tag=0x10, data=b'\x00' * 13)
        self.raw = bytes(18)


# ═══════════════════════════════════════════════════════════════════════════
# CEA-861 Data Blocks
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class ShortVideoDescriptor:
    """SVD - 短视频描述符 (1 字节)"""
    vic: int = 0          # Video Identification Code
    native: bool = False  # 是否为原生格式

    @classmethod
    def from_byte(cls, b: int) -> 'ShortVideoDescriptor':
        return cls(vic=b & 0x7F, native=bool(b & 0x80))

    def to_byte(self) -> int:
        return (self.vic & 0x7F) | (0x80 if self.native else 0)

    @property
    def description(self) -> str:
        desc = VIC_TABLE.get(self.vic, f"VIC {self.vic}")
        return f"{desc}{' (原生)' if self.native else ''}"


@dataclass
class AudioFormat:
    """单个音频格式描述符 (3 字节)"""
    format_code: int = 1    # 1=LPCM, 2=AC-3, 6=DTS, 7=DTS-HD, etc.
    max_channels: int = 2
    sample_rates: int = 0   # bitmask: 32/44.1/48/88.2/96/176.4/192 kHz
    bit_depth: int = 0      # bitmask: 16/20/24 bit

    FORMAT_NAMES = {
        1: "LPCM", 2: "AC-3", 3: "MPEG-1", 4: "MP3",
        5: "MPEG-2", 6: "AAC", 7: "DTS", 8: "ATRAC",
        9: "DSD", 10: "E-AC-3", 11: "DTS-HD", 12: "Dolby TrueHD",
        13: "DST", 14: "WMA Pro", 15: "HE-AAC",
    }

    @property
    def format_name(self) -> str:
        return self.FORMAT_NAMES.get(self.format_code & 0x0F, f"格式 {self.format_code}")

    def sample_rate_list(self) -> List[str]:
        rates = {0: "32k", 1: "44.1k", 2: "48k", 3: "88.2k",
                 4: "96k", 5: "176.4k", 6: "192k"}
        return [r for i, r in rates.items() if self.sample_rates & (1 << i)]

    @classmethod
    def from_bytes(cls, data: bytes) -> 'AudioFormat':
        if len(data) < 3:
            return cls()
        return cls(
            format_code=(data[0] >> 3) & 0x0F,
            max_channels=(data[0] & 0x07) + 1,
            sample_rates=data[1],
            bit_depth=data[2],
        )


@dataclass
class DataBlock:
    """CEA-861 数据块基类"""
    tag: int = 0
    ext_tag: int = 0  # 仅 tag==7 (extended) 时有效
    raw_data: bytes = field(default_factory=bytes)

    @property
    def tag_name(self) -> str:
        names = {
            1: "音频数据块 (Audio)",
            2: "视频数据块 (SVD)",
            3: "厂商特定 (VSDB)",
            4: "扬声器分配 (Speaker)",
            5: "VESA DTC",
            7: "扩展标签",
        }
        base = names.get(self.tag, f"未知({self.tag})")
        if self.tag == 7:
            ext_names = {
                0: "视频能力 (Video Capability)",
                1: "厂商特定视频 (Vendor Video)",
                5: "色彩学 (Colorimetry)",
                6: "HDR 静态元数据 (HDR Static)",
                7: "HDR 动态元数据 (HDR Dynamic)",
                14: "YCbCr 4:2:0 视频",
                15: "YCbCr 4:2:0 能力映射",
                17: "厂商特定音频 (Vendor Audio)",
            }
            return ext_names.get(self.ext_tag, f"扩展标签(0x{self.ext_tag:02X})")
        return base

    @property
    def summary(self) -> str:
        """子类可覆盖以提供摘要"""
        return f"{len(self.raw_data)} 字节"

    @classmethod
    def parse_collection(cls, data: bytes, max_offset: int) -> List['DataBlock']:
        """解析 Data Block Collection"""
        blocks = []
        offset = 0
        while offset < max_offset and offset < len(data):
            header = data[offset]
            tag = (header >> 5) & 0x07
            length = header & 0x1F
            if tag == 0 and length == 0:
                break
            end = offset + 1 + length
            if end > max_offset or end > len(data):
                break
            raw = bytes(data[offset + 1:end])
            block = DataBlock._parse_one(tag, raw)
            if block:
                blocks.append(block)
            offset = end
        return blocks

    @classmethod
    def _parse_one(cls, tag: int, raw: bytes) -> Optional['DataBlock']:
        if tag == CEA_DB_AUDIO and len(raw) >= 3:
            return AudioDataBlock.create(raw)
        if tag == CEA_DB_VIDEO:
            return VideoDataBlock.create(raw)
        if tag == CEA_DB_VENDOR:
            return VendorDataBlock.create(raw)
        if tag == CEA_DB_SPEAKER:
            return SpeakerDataBlock.create(raw)
        if tag == CEA_DB_EXTENDED and len(raw) >= 1:
            ext_tag = raw[0]
            # 路由到专门的扩展标签解析器
            if ext_tag == CEA_EXT_HDR_STATIC:
                return HDRStaticMetadataBlock.create(raw)
            if ext_tag == CEA_EXT_COLORIMETRY:
                return ColorimetryBlock.create(raw)
            if ext_tag == CEA_EXT_YCBCR420_CAPMAP:
                return YCbCr420CapMapBlock.create(raw)
            if ext_tag == CEA_EXT_VIDEO_CAP:
                return VideoCapabilityBlock.create(raw)
            if ext_tag == CEA_EXT_YCBCR420_VIDEO:
                return YCbCr420VideoBlock.create(raw)
            return DataBlock(tag=tag, ext_tag=ext_tag, raw_data=raw)
        return DataBlock(tag=tag, raw_data=raw)


class AudioDataBlock(DataBlock):
    """CEA 音频数据块"""
    def __init__(self, formats: List[AudioFormat] = None):
        super().__init__(tag=CEA_DB_AUDIO)
        self.formats = formats or []

    @classmethod
    def create(cls, raw: bytes) -> 'AudioDataBlock':
        fmts = []
        for i in range(0, len(raw) - 2, 3):
            fmts.append(AudioFormat.from_bytes(raw[i:i+3]))
        return cls(fmts)

    @property
    def summary(self) -> str:
        if not self.formats:
            return "无音频"
        parts = []
        for f in self.formats[:4]:  # 最多显示 4 个
            parts.append(f"{f.format_name} ({f.max_channels}ch)")
        if len(self.formats) > 4:
            parts.append(f"...共 {len(self.formats)} 种格式")
        return ", ".join(parts)


class VideoDataBlock(DataBlock):
    """CEA 视频数据块 (Short Video Descriptors)"""
    def __init__(self, svds: List[ShortVideoDescriptor] = None):
        super().__init__(tag=CEA_DB_VIDEO)
        self.svds = svds or []

    @classmethod
    def create(cls, raw: bytes) -> 'VideoDataBlock':
        svds = [ShortVideoDescriptor.from_byte(b) for b in raw]
        return cls(svds)

    @property
    def summary(self) -> str:
        if not self.svds:
            return "无"
        parts = []
        for s in self.svds[:6]:
            parts.append(s.description)
        if len(self.svds) > 6:
            parts.append(f"...共 {len(self.svds)} 个")
        return " | ".join(parts)


class VendorDataBlock(DataBlock):
    """厂商特定数据块 (HDMI VSDB 等)"""
    def __init__(self, ieee_oui: int = 0, payload: bytes = b''):
        super().__init__(tag=CEA_DB_VENDOR)
        self.ieee_oui = ieee_oui
        self.payload = payload

    @classmethod
    def create(cls, raw: bytes) -> 'VendorDataBlock':
        if len(raw) >= 3:
            oui = raw[0] | (raw[1] << 8) | (raw[2] << 16)
            return cls(ieee_oui=oui, payload=bytes(raw[3:]))
        return cls(payload=raw)

    @property
    def vendor_name(self) -> str:
        if self.ieee_oui == 0x000C03:
            return "HDMI"
        elif self.ieee_oui == 0xC45DD8:
            return "HDMI Forum"
        elif self.ieee_oui == 0x0050F2:
            return "DisplayPort"
        return f"OUI 0x{self.ieee_oui:06X}"

    @property
    def summary(self) -> str:
        parts = [self.vendor_name]
        if self.ieee_oui == HDMI_OUI and len(self.payload) >= 5:
            # HDMI 1.4 VSDB
            b0 = self.payload[0]
            src_phy = 'HDMI-A' if (b0 & 0x80) else ('HDMI-B' if (b0 & 0x01) else 'DVI')
            max_tmds = "165MHz" if (self.payload[2] & 0x20) else "340MHz" if (self.payload[2] & 0x40) else "?"
            parts.append(f"物理接口={src_phy}, Max TMDS={max_tmds}")
            if b0 & 0x20: parts.append("Deep Color 36bit")
            if b0 & 0x40: parts.append("Deep Color 48bit")
            if b0 & 0x10: parts.append("支持 AI (ACP/ISRC)")
            if len(self.payload) >= 6 and self.payload[4] & 0x04:
                parts.append("3D 支持")
            if len(self.payload) >= 8 and self.payload[6] & 0x80:
                parts.append("4K×2K 支持 (HDMI 1.4b)")
        elif self.ieee_oui == 0xC45DD8 and len(self.payload) >= 1:
            # HDMI Forum VSDB (HDMI 2.0/2.1)
            ver = self.payload[0] & 0x07
            ver_label = {0: "保留", 1: "HDMI 2.0", 2: "HDMI 2.1"}.get(ver, f"未知(ver={ver})")
            parts.append(ver_label)
            if len(self.payload) >= 6:
                # Max TMDS Character Rate (5 Gc/s units + 300 Mcsc)
                max_tmds = self.payload[4] & 0x7F
                if max_tmds:
                    val = max_tmds * 5 + 300
                    parts.append(f"Max TMDS={val}Mcsc")
                # SCDC present
                if self.payload[5] & 0x08:
                    parts.append("SCDC 支持")
                # Scrambling < 340 Mcsc
                if self.payload[5] & 0x02:
                    parts.append("LTE 340Mcsc Scramble")
                # HDR
                if self.payload[5] & 0x04:
                    parts.append("HDR 支持 (HDMI 2.0a/b)")
                # Max FRL Rate (HDMI 2.1)
                if len(self.payload) >= 7:
                    max_frl = (self.payload[6] >> 4) & 0x0F
                    frl_rates = {0: "无FRL", 1: "3 Gbps×3 (9G)", 2: "6 Gbps×3 (18G)",
                                 3: "6 Gbps×4 (24G)", 4: "8 Gbps×4 (32G)",
                                 5: "10 Gbps×4 (40G)", 6: "12 Gbps×4 (48G)"}
                    if max_frl:
                        parts.append(f"FRL={frl_rates.get(max_frl, f'{max_frl}')}")
                    # DSC support
                    if self.payload[6] & 0x08:
                        parts.append("DSC 支持")
                # DSC Max FRL Rate
                if len(self.payload) >= 8 and self.payload[6] & 0x08:
                    dsc_max_frl = self.payload[7] & 0x0F
                    if dsc_max_frl:
                        parts.append(f"DSC FRL={dsc_max_frl}")
        return " | ".join(parts)


class SpeakerDataBlock(DataBlock):
    """扬声器分配数据块"""
    SPEAKER_BITS = {
        0: "FL/FR (前置左右)", 1: "LFE (低音)",
        2: "FC (前置中置)", 3: "RL/RR (后置左右)",
        4: "RC (后置中置)", 5: "FLC (前置左中)", 6: "FRC (前置右中)",
    }

    def __init__(self, allocation: int = 0):
        super().__init__(tag=CEA_DB_SPEAKER)
        self.allocation = allocation

    @classmethod
    def create(cls, raw: bytes) -> 'SpeakerDataBlock':
        alloc = raw[0] | (raw[1] << 8) | (raw[2] << 16) if len(raw) >= 3 else (raw[0] if raw else 0)
        return cls(allocation=alloc)

    @property
    def summary(self) -> str:
        if self.allocation == 0:
            return "立体声 (2.0)"
        parts = []
        for bit, name in self.SPEAKER_BITS.items():
            if self.allocation & (1 << bit):
                parts.append(name)
        return ", ".join(parts) if parts else f"0x{self.allocation:06X}"


# ── 扩展标签数据块 ─────────────────────────────────────────────────────
class VideoCapabilityBlock(DataBlock):
    """视频能力数据块 (ext tag 0) — 每个字节按 CEA-861 定义"""
    def __init__(self, raw: bytes = b''):
        super().__init__(tag=CEA_DB_EXTENDED, ext_tag=CEA_EXT_VIDEO_CAP, raw_data=raw)
        self.selectable_ycc_quant = bool(raw[1] & 0x40) if len(raw) > 1 else False
        self.selectable_rgb_quant = bool(raw[1] & 0x80) if len(raw) > 1 else False
        self.pt_overscan = bool(raw[1] & 0x20) if len(raw) > 1 else False
        self.it_overscan = bool(raw[1] & 0x10) if len(raw) > 1 else False
        self.ce_overscan = bool(raw[1] & 0x08) if len(raw) > 1 else False

    @classmethod
    def create(cls, raw: bytes) -> 'VideoCapabilityBlock':
        return cls(raw)

    @property
    def summary(self) -> str:
        parts = []
        if self.selectable_rgb_quant: parts.append("可选RGB量化范围")
        if self.selectable_ycc_quant: parts.append("可选YCC量化范围")
        if self.pt_overscan: parts.append("PT过扫描")
        if self.it_overscan: parts.append("IT过扫描")
        if self.ce_overscan: parts.append("CE过扫描")
        return ", ".join(parts) if parts else "无特殊能力"


class ColorimetryBlock(DataBlock):
    """色彩学数据块 (ext tag 5)"""
    COLORIMETRY = {   # byte 2 bitmask
        0: "BT.2020 YCC", 1: "BT.2020 RGB", 2: "BT.2020 cRGB",
        3: "DCI-P3 (ST 428)", 4: "xvYCC709", 5: "xvYCC601",
        6: "sYCC601", 7: "Adobe YCC601", 8: "Adobe RGB (OP)",
        9: "BT.2020 CYCC",
    }

    def __init__(self, raw: bytes = b''):
        super().__init__(tag=CEA_DB_EXTENDED, ext_tag=CEA_EXT_COLORIMETRY, raw_data=raw)

    @classmethod
    def create(cls, raw: bytes) -> 'ColorimetryBlock':
        return cls(raw)

    @property
    def supported(self) -> List[str]:
        if len(self.raw_data) < 3:
            return []
        bm = self.raw_data[2]
        result = []
        for bit, name in self.COLORIMETRY.items():
            if bm & (1 << bit):
                result.append(name)
        return result

    @property
    def summary(self) -> str:
        s = self.supported
        return "支持: " + (", ".join(s) if s else "无额外色彩学")


class HDRStaticMetadataBlock(DataBlock):
    """HDR 静态元数据数据块 (ext tag 6)"""
    EOTF_NAMES = {0: "传统 Gamma (SDR)", 1: "SMPTE ST 2084 (PQ)", 2: "HLG (Hybrid Log-Gamma)"}

    def __init__(self, raw: bytes = b''):
        super().__init__(tag=CEA_DB_EXTENDED, ext_tag=CEA_EXT_HDR_STATIC, raw_data=raw)
        self.eotfs: List[int] = []  # supported EOTF list
        self.sdr_eotf: int = 0
        self.descriptor_types: List[int] = []
        if len(raw) >= 3:
            self.sdr_eotf = raw[1]
            eotf_bm = raw[2]
            for bit in range(8):
                if eotf_bm & (1 << bit):
                    self.eotfs.append(bit)
        # 解析 Static Metadata Descriptor Type 1
        if len(raw) >= 4:
            pos = 3
            while pos < len(raw):
                dtype = raw[pos]
                if dtype == 0:
                    break
                dlen = raw[pos + 1] if pos + 1 < len(raw) else 0
                self.descriptor_types.append(dtype)
                pos += 2 + dlen

    @classmethod
    def create(cls, raw: bytes) -> 'HDRStaticMetadataBlock':
        return cls(raw)

    @property
    def summary(self) -> str:
        parts = []
        if self.eotfs:
            eotf_names = [self.EOTF_NAMES.get(e, f"EOTF {e}") for e in self.eotfs]
            parts.append("HDR EOTF: " + ", ".join(eotf_names))
        if self.descriptor_types:
            parts.append(f"描述符类型: {self.descriptor_types}")
        sdr = self.EOTF_NAMES.get(self.sdr_eotf, f"Type {self.sdr_eotf}")
        parts.append(f"SDR={sdr}")
        return " | ".join(parts) if parts else "HDR 静态元数据"


class YCbCr420CapMapBlock(DataBlock):
    """YCbCr 4:2:0 能力映射数据块 (ext tag 15)"""
    def __init__(self, raw: bytes = b''):
        super().__init__(tag=CEA_DB_EXTENDED, ext_tag=CEA_EXT_YCBCR420_CAPMAP, raw_data=raw)

    @classmethod
    def create(cls, raw: bytes) -> 'YCbCr420CapMapBlock':
        return cls(raw)

    @property
    def summary(self) -> str:
        if len(self.raw_data) < 2:
            return "YCbCr 4:2:0 能力映射"
        # raw_data[1:] 是支持的 SVD 索引位掩码
        supported_svds = []
        for i in range(1, len(self.raw_data)):
            for bit in range(8):
                if self.raw_data[i] & (1 << bit):
                    svd_idx = (i - 1) * 8 + bit
                    supported_svds.append(str(svd_idx))
        return f"YCbCr 4:2:0 支持 {len(supported_svds)} 个 VIC" + (f" (索引: {', '.join(supported_svds[:10])}{'...' if len(supported_svds) > 10 else ''})" if supported_svds else "")


class YCbCr420VideoBlock(DataBlock):
    """YCbCr 4:2:0 视频数据块 (ext tag 14) — 列出仅 4:2:0 下支持的 VIC"""
    def __init__(self, raw: bytes = b''):
        super().__init__(tag=CEA_DB_EXTENDED, ext_tag=CEA_EXT_YCBCR420_VIDEO, raw_data=raw)

    @classmethod
    def create(cls, raw: bytes) -> 'YCbCr420VideoBlock':
        return cls(raw)

    @property
    def summary(self) -> str:
        if len(self.raw_data) < 2:
            return "YCbCr 4:2:0 视频"
        vics = []
        for i in range(1, len(self.raw_data)):
            vic = self.raw_data[i] & 0x7F
            desc = VIC_TABLE.get(vic, f"VIC {vic}")
            vics.append(desc)
        return "YCbCr 4:2:0 视频: " + (" | ".join(vics[:8]) + ("..." if len(vics) > 8 else "") if vics else "空")


# ═══════════════════════════════════════════════════════════════════════════
# CEA-861 扩展块
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class CEA861Extension:
    """CEA-861 扩展块 (128 字节)

    结构:
      Byte 0:      标签 (0x02)
      Byte 1:      版本号
      Byte 2:      DTD 起始偏移 (距本块起始)
      Byte 3:      标志位
      Bytes 4~:    Data Block Collection (变长)
      DTDs:        详细时序描述符 (18 字节 × N)
      Byte 127:    校验和
    """
    revision: int = 3
    dtd_offset: int = 4
    underscan: bool = False
    basic_audio: bool = False
    ycbcr444: bool = False
    ycbcr422: bool = False
    native_dtd_count: int = 0
    data_blocks: List[DataBlock] = field(default_factory=list)
    dtds: List[DetailedTiming] = field(default_factory=list)
    _raw: bytes = field(default_factory=bytes)

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional['CEA861Extension']:
        if len(data) < 128 or data[0] != CEA_TAG:
            return None

        rev = data[1]
        dtd_off = data[2]
        flags = data[3]

        ext = cls(
            revision=rev,
            dtd_offset=dtd_off,
            underscan=bool(flags & 0x80),
            basic_audio=bool(flags & 0x40),
            ycbcr444=bool(flags & 0x20),
            ycbcr422=bool(flags & 0x10),
            native_dtd_count=flags & 0x0F,
            _raw=data,
        )

        # 解析 Data Block Collection
        db_end = min(dtd_off, 127)
        if db_end > 4:
            ext.data_blocks = DataBlock.parse_collection(data[4:db_end], db_end - 4)

        # 解析 DTDs
        pos = dtd_off
        while pos + 18 <= 127:
            dtd = DetailedTiming.from_bytes(data[pos:pos + 18])
            if dtd:
                ext.dtds.append(dtd)
                pos += 18
            else:
                break

        return ext

    def to_bytes(self) -> bytes:
        """序列化为 128 字节"""
        buf = bytearray(128)
        buf[0] = CEA_TAG
        buf[1] = self.revision

        # 先序列化 Data Block Collection
        db_data = bytearray()
        for db in self.data_blocks:
            if db.tag == CEA_DB_AUDIO and hasattr(db, 'formats'):
                # Audio block
                raw = bytearray()
                for af in db.formats:
                    raw.append(((af.format_code & 0x0F) << 3) | ((af.max_channels - 1) & 0x07))
                    raw.append(af.sample_rates)
                    raw.append(af.bit_depth)
                db_data.append(((CEA_DB_AUDIO & 0x07) << 5) | (len(raw) & 0x1F))
                db_data.extend(raw)
            elif db.tag == CEA_DB_VIDEO and hasattr(db, 'svds'):
                raw = bytearray()
                for svd in db.svds:
                    raw.append(svd.to_byte())
                db_data.append(((CEA_DB_VIDEO & 0x07) << 5) | (len(raw) & 0x1F))
                db_data.extend(raw)
            elif db.tag == CEA_DB_VENDOR and hasattr(db, 'ieee_oui'):
                raw = bytearray()
                raw.append(db.ieee_oui & 0xFF)
                raw.append((db.ieee_oui >> 8) & 0xFF)
                raw.append((db.ieee_oui >> 16) & 0xFF)
                raw.extend(db.payload)
                db_data.append(((CEA_DB_VENDOR & 0x07) << 5) | (len(raw) & 0x1F))
                db_data.extend(raw)
            elif db.tag == CEA_DB_SPEAKER and hasattr(db, 'allocation'):
                raw = bytearray()
                raw.append(db.allocation & 0xFF)
                raw.append((db.allocation >> 8) & 0xFF)
                raw.append((db.allocation >> 16) & 0xFF)
                db_data.append(((CEA_DB_SPEAKER & 0x07) << 5) | (len(raw) & 0x1F))
                db_data.extend(raw)
            elif db.tag == CEA_DB_EXTENDED and db.raw_data:
                data_with_ext = bytes([db.ext_tag]) + db.raw_data[1:]
                db_data.append(((7 & 0x07) << 5) | (len(data_with_ext) & 0x1F))
                db_data.extend(data_with_ext)
            elif db.raw_data:
                db_data.append(((db.tag & 0x07) << 5) | (len(db.raw_data) & 0x1F))
                db_data.extend(db.raw_data)

        # 计算 DTD 偏移
        dtd_offset = 4 + len(db_data)
        # DTD offset 必须是 18 字节对齐
        buf[2] = dtd_offset

        # 标志
        flags = 0
        if self.underscan: flags |= 0x80
        if self.basic_audio: flags |= 0x40
        if self.ycbcr444: flags |= 0x20
        if self.ycbcr422: flags |= 0x10
        flags |= self.native_dtd_count & 0x0F
        buf[3] = flags

        # 写入 Data Block Collection
        buf[4:4 + len(db_data)] = db_data

        # 写入 DTDs
        pos = dtd_offset
        for dtd in self.dtds:
            if pos + 18 <= 127:
                buf[pos:pos + 18] = dtd.to_bytes()
                pos += 18

        # 校验和
        buf[127] = (256 - sum(buf[:127])) % 256
        return bytes(buf)

    def is_checksum_valid(self) -> bool:
        return sum(self._raw[:128]) % 256 == 0 if len(self._raw) >= 128 else False

    def get_video_svds(self) -> List[ShortVideoDescriptor]:
        """获取所有 SVD (短视频描述符) — 跨所有 VideoDataBlock 累积"""
        svds = []
        for db in self.data_blocks:
            if isinstance(db, VideoDataBlock):
                svds.extend(db.svds)
        return svds

    def get_hdmi_vsdb(self) -> Optional[VendorDataBlock]:
        """获取 HDMI VSDB"""
        for db in self.data_blocks:
            if isinstance(db, VendorDataBlock) and db.ieee_oui == HDMI_OUI:
                return db
        return None

    def get_audio_formats(self) -> List[AudioFormat]:
        """获取所有音频格式 — 跨所有 AudioDataBlock 累积"""
        formats = []
        for db in self.data_blocks:
            if isinstance(db, AudioDataBlock):
                formats.extend(db.formats)
        return formats

    @classmethod
    def create_minimal(cls) -> 'CEA861Extension':
        """创建最小 CEA-861 扩展块 (60Hz 1080p + 基本音频)"""
        ext = cls(revision=3, basic_audio=True, underscan=True, ycbcr444=True, ycbcr422=True)
        # 添加一个 SVD: 1080p60
        ext.data_blocks.append(VideoDataBlock(svds=[
            ShortVideoDescriptor(vic=16, native=True),   # 1080p60
            ShortVideoDescriptor(vic=4),                  # 720p60
            ShortVideoDescriptor(vic=1),                  # 480p60
        ]))
        # 添加基本音频
        ext.data_blocks.append(AudioDataBlock(formats=[
            AudioFormat(format_code=1, max_channels=2,
                        sample_rates=(1<<2)|(1<<3),     # 48k + 44.1k
                        bit_depth=(1<<1)|(1<<2)),        # 20bit + 24bit
        ]))
        # 添加扬声器分配 (立体声)
        ext.data_blocks.append(SpeakerDataBlock(allocation=0))
        # 添加 HDMI VSDB (最小)
        # IEEE OUI 0x000C03 + 2 bytes
        ext.data_blocks.append(VendorDataBlock(
            ieee_oui=HDMI_OUI,
            payload=bytes([0x80, 0x10, 0x00]),  # HDMI-A, supports AI
        ))
        return ext


# ═══════════════════════════════════════════════════════════════════════════
# DisplayID 扩展块 (tag 0x70) — VESA 新一代显示器识别标准
# ═══════════════════════════════════════════════════════════════════════════

# DisplayID Data Block 标签
DID_DB_PRODUCT_ID = 0x01       # 产品标识
DID_DB_DISPLAY_PARAM = 0x02    # 显示参数
DID_DB_TIMING_TYPE_I = 0x03    # Type I 详细时序 (20字节载荷)
DID_DB_TIMING_TYPE_II = 0x04   # Type II 详细时序
DID_DB_TIMING_TYPE_III = 0x05  # Type III 详细时序 (包含像素格式)
DID_DB_TIMING_TYPE_IV = 0x06   # Type IV 详细时序
DID_DB_TIMING_TYPE_V = 0x07    # Type V 详细时序 (立体)
DID_DB_TIMING_TYPE_VI = 0x08   # Type VI 详细时序
DID_DB_TIMING_TYPE_VII = 0x0D  # Type VII 详细时序
DID_DB_TIMING_TYPE_VIII = 0x0E # Type VIII 详细时序
DID_DB_TILED_DISPLAY = 0x09    # 拼接显示拓扑
DID_DB_DISPLAY_DEVICE = 0x0A   # 显示设备
DID_DB_INTERFACE_POWER = 0x0B  # 接口电源序列
DID_DB_TRANSFER_CHAR = 0x0C    # 传输特性 (Gamma/EOTF)
DID_DB_STEREO = 0x0F           # 立体显示接口
DID_DB_DYNAMIC_RANGE = 0x10    # 动态视频时序范围限制
DID_DB_CONTAINER_ID = 0x21     # 容器 ID (UUID)
DID_DB_VENDOR_SPECIFIC = 0x22  # 厂商特定
DID_DB_PRODUCT_ID_EXT = 0x23   # 产品标识扩展
DID_DB_ADAPTIVE_SYNC = 0x24    # Adaptive Sync (FreeSync/G-Sync)
DID_DB_HDR_STATIC = 0x25       # HDR 静态元数据 (DisplayID 版)
DID_DB_BLOCK_MAP = 0x7F        # 块映射

DID_TAG_NAMES = {
    0x01: "产品标识 (Product ID)", 0x02: "显示参数 (Display Parameters)",
    0x03: "Type I 详细时序", 0x04: "Type II 详细时序",
    0x05: "Type III 详细时序", 0x06: "Type IV 详细时序",
    0x07: "Type V 详细时序", 0x08: "Type VI 详细时序",
    0x09: "拼接显示拓扑 (Tiled)", 0x0A: "显示设备 (Display Device)",
    0x0B: "接口电源序列", 0x0C: "传输特性 (Transfer Char)",
    0x0D: "Type VII 详细时序", 0x0E: "Type VIII 详细时序",
    0x0F: "立体显示接口", 0x10: "动态时序范围",
    0x21: "容器 ID (UUID)", 0x22: "厂商特定 (Vendor)",
    0x23: "产品标识扩展", 0x24: "Adaptive Sync (FreeSync/G-Sync)",
    0x25: "HDR 静态元数据", 0x7F: "块映射 (Block Map)",
}

DID_TIMING_TAGS = {0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0D, 0x0E}


@dataclass
class DisplayIDDataBlock:
    """DisplayID 数据块 (变长, 最少2字节头部)"""
    tag: int = 0
    revision: int = 0
    payload: bytes = field(default_factory=bytes)

    @classmethod
    def from_bytes(cls, data: bytes) -> 'DisplayIDDataBlock':
        tag = data[0]
        rev = (data[1] >> 5) & 0x07
        length = data[1] & 0x1F  # 载荷字节数
        payload = bytes(data[2:2 + length]) if length > 0 else b''
        return cls(tag=tag, revision=rev, payload=payload)

    @property
    def tag_name(self) -> str:
        return DID_TAG_NAMES.get(self.tag, f"未知标签 (0x{self.tag:02X})")

    @property
    def is_timing(self) -> bool:
        return self.tag in DID_TIMING_TAGS

    @property
    def summary(self) -> str:
        """子类覆盖以提供更详细信息"""
        if self.is_timing:
            t = self.parse_timing()
            if t:
                return (f"{t.h_active}×{t.v_active} @ {t.refresh_rate:.1f}Hz "
                        f"({t.pixel_clock_mhz:.1f}MHz)")
            return f"{self.tag_name} ({len(self.payload)} 字节)"
        if self.tag == DID_DB_PRODUCT_ID:
            return self._parse_product_id()
        if self.tag == DID_DB_ADAPTIVE_SYNC:
            return self._parse_adaptive_sync()
        if self.tag == DID_DB_TILED_DISPLAY:
            return f"拼接显示 ({len(self.payload)} 字节)"
        return f"{len(self.payload)} 字节"

    def parse_timing(self) -> Optional[DetailedTiming]:
        """尝试解析 DisplayID 详细时序为通用的 DetailedTiming 对象"""
        if len(self.payload) < 20:
            return None
        p = self.payload
        # Pixel Clock: bytes 0-3, little-endian uint32
        # DisplayID 1.2: 10kHz 单位; DisplayID 1.3: Hz 单位
        # 我们通过数值大小判断：> 100000 则可能是 Hz，转换为 10kHz
        pclk_raw = p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24)
        if pclk_raw > 500000:  # 超过 5GHz → 可能是 Hz 单位
            pclk = pclk_raw // 10000
        else:
            pclk = pclk_raw

        # H Active: bytes 4-5, little-endian uint16
        ha = p[4] | (p[5] << 8)

        # H Blank: byte 6 (low 8 bits) + byte 7 bits [3:0] (high 4 bits)
        hb = p[6] | ((p[7] & 0x0F) << 8)

        # H Front Porch (sync offset): byte 8 [7:0] low + byte 7 bits [7:4] high nibble
        hfp = p[8] | (((p[7] >> 4) & 0x0F) << 8)

        # H Sync Width: byte 9
        hs = p[9]

        # V Active: bytes 10-11, little-endian uint16
        va = p[10] | (p[11] << 8)

        # V Blank: byte 12 low + byte 13 bits [3:0] high nibble
        vb = p[12] | ((p[13] & 0x0F) << 8)

        # V Front Porch: byte 14 [7:0] low + byte 13 bits [7:4] high nibble
        vfp = p[14] | (((p[13] >> 4) & 0x0F) << 8)

        # V Sync Width: byte 15
        vs = p[15]

        # Image Size: bytes 16-17 (H), bytes 18-19 (V), mm
        hsz = p[16] | (p[17] << 8)
        vsz = p[18] | (p[19] << 8)

        return DetailedTiming(
            pixel_clock=pclk, h_active=ha, h_blanking=hb,
            v_active=va, v_blanking=vb,
            h_front_porch=hfp, h_sync=hs,
            v_front_porch=vfp, v_sync=vs,
            h_image_size=hsz, v_image_size=vsz,
        )

    def _parse_product_id(self) -> str:
        """解析产品标识数据块"""
        if len(self.payload) < 12:
            return f"产品标识 ({len(self.payload)} 字节)"
        p = self.payload
        # Byte 0-1: Manufacturer PnP ID (同 EDID 编码)
        l1 = (p[0] >> 2) & 0x1F
        l2 = ((p[0] & 0x03) << 3) | ((p[1] >> 5) & 0x07)
        l3 = p[1] & 0x1F
        try:
            mfr = chr(l1 + ord('A') - 1) + chr(l2 + ord('A') - 1) + chr(l3 + ord('A') - 1)
        except ValueError:
            mfr = "???"
        # Byte 2-3: Product Code (LE)
        prod = p[2] | (p[3] << 8)
        # Byte 4-7: Serial Number (LE)
        sn = p[4] | (p[5] << 8) | (p[6] << 16) | (p[7] << 24)
        # Byte 8: Week, Byte 9: Year
        week = p[8] if len(p) > 8 else 0
        year = (p[9] + 2000) if len(p) > 9 else 0
        # Byte 10+: Model tag / name string
        model_str = ""
        if len(p) > 10:
            try:
                model_str = p[10:].decode('ascii', errors='replace').rstrip('\x00\n ')
            except Exception:
                pass
        parts = [f"Mfr={mfr}", f"Prod=0x{prod:04X}", f"SN=0x{sn:08X}"]
        if week and year:
            parts.append(f"Date={year}W{week}")
        if model_str:
            parts.append(f'Model="{model_str}"')
        return " | ".join(parts)

    def _parse_adaptive_sync(self) -> str:
        """解析 Adaptive Sync 数据块"""
        if len(self.payload) < 2:
            return "Adaptive Sync"
        p = self.payload
        # Byte 0: Max refresh (Hz), Byte 1: Min refresh (Hz)
        max_rr = p[0] if p[0] else 0
        min_rr = p[1] if len(p) > 1 else 0
        if min_rr and max_rr:
            return f"Adaptive Sync: {min_rr}-{max_rr} Hz"
        return f"Adaptive Sync ({len(self.payload)} 字节)"


@dataclass
class DisplayIDExtension:
    """DisplayID 扩展块 (tag 0x70, 128 字节)

    结构:
      Byte 0:      标签 (0x70)
      Byte 1:      版本号 (如 0x13 = 1.3)
      Byte 2:      数据块数量
      Bytes 3-4:   保留
      Bytes 5+:    DisplayID Data Blocks (变长序列)
      Byte 127:    校验和
    """
    revision: int = 0x13
    data_blocks: List[DisplayIDDataBlock] = field(default_factory=list)
    _raw: bytes = field(default_factory=bytes)
    _vendor_raw_start: int = 0  # 非标准数据起始字节位置

    @property
    def timings(self) -> List[Tuple[DisplayIDDataBlock, DetailedTiming]]:
        """提取所有 DisplayID 详细时序"""
        result = []
        for db in self.data_blocks:
            if db.is_timing:
                t = db.parse_timing()
                if t:
                    result.append((db, t))
        return result

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional['DisplayIDExtension']:
        if len(data) < 128 or data[0] != DISPLAYID_TAG:
            return None
        rev = data[1]
        ext = cls(revision=rev, _raw=data)

        # DisplayID 版本决定 data block 起始偏移
        # 1.3+: 从 byte 5 开始
        # 1.2:  从 byte 3 开始 (byte 2 = section length / flags)
        if rev >= 0x13:
            pos = 5
        else:
            pos = 3  # DisplayID 1.2 and earlier

        parsed_count = 0
        while pos < 126:
            if pos + 2 > 126:
                break
            tag = data[pos]
            byte1 = data[pos + 1]
            length = byte1 & 0x1F
            # 合法性检查：非标准 tag 或异常长度 → 可能是 vendor 自定义数据，停止解析
            if tag > 0x7F or (tag == 0 and length == 0):
                break
            if tag < 0x01 or (tag > 0x25 and tag < 0x7F and tag not in {0x7F}):
                break  # 未知标签范围 → 非标准数据
            end = pos + 2 + length
            if end > 127:
                end = 127
            chunk = bytes(data[pos:end])
            try:
                db = DisplayIDDataBlock.from_bytes(chunk)
                # 过滤无效数据块：时序类型必须有足够载荷
                if not (db.tag in DID_TIMING_TAGS and len(db.payload) < 1):
                    ext.data_blocks.append(db)
                    parsed_count += 1
            except Exception:
                break
            pos = end
            if parsed_count > 32:  # 安全上限
                break

        # 剩余数据作为 raw vendor data
        ext._vendor_raw_start = pos
        return ext

    def to_bytes(self) -> bytes:
        """序列化为 128 字节"""
        buf = bytearray(128)
        buf[0] = DISPLAYID_TAG
        buf[1] = self.revision
        buf[2] = len(self.data_blocks) & 0xFF
        pos = 5
        for db in self.data_blocks:
            size = 2 + len(db.payload)
            if pos + size > 127:
                break
            buf[pos] = db.tag
            buf[pos + 1] = ((db.revision & 0x07) << 5) | (len(db.payload) & 0x1F)
            buf[pos + 2:pos + size] = db.payload
            pos += size
        buf[127] = (256 - sum(buf[:127])) % 256
        return bytes(buf)

    @property
    def vendor_raw(self) -> bytes:
        """返回未解析的 vendor 自定义数据"""
        if self._vendor_raw_start > 0 and len(self._raw) > self._vendor_raw_start:
            return self._raw[self._vendor_raw_start:127]
        return b''

    def is_checksum_valid(self) -> bool:
        return sum(self._raw[:128]) % 256 == 0 if len(self._raw) >= 128 else False

    @property
    def summary(self) -> str:
        timing_count = sum(1 for db in self.data_blocks if db.is_timing)
        parts = [f"DisplayID 1.{self.revision >> 4}{(self.revision & 0x0F)}",
                 f"{len(self.data_blocks)} 个数据块",
                 f"{timing_count} 个详细时序"]
        return " | ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# EDID 主类 — 支持 128/256/384 字节
# ═══════════════════════════════════════════════════════════════════════════
class EDID:
    """EDID 数据结构解析器与编辑器

    支持:
      - 128 字节基础 EDID (Block 0)
      - 256 字节 EDID (Block 0 + 1 extension)
      - 384 字节 EDID (Block 0 + 2 extensions)
      - CEA-861 扩展块解析
      - RTD 固件文件中自动定位/提取 EDID
    """

    def __init__(self, data: Optional[bytes] = None, total_blocks: int = 1):
        """初始化 EDID

        Args:
            data: 原始 EDID 数据 (≥128 字节)
            total_blocks: 总块数 (1=128B, 2=256B, 3=384B)
        """
        if data is None:
            self._blocks: List[bytearray] = [bytearray(EDID_BLOCK_SIZE) for _ in range(total_blocks)]
            self._blocks[0][0:8] = EDID_HEADER
            self._blocks[0][18] = 1
            self._blocks[0][19] = 4
        else:
            self._blocks = []
            for i in range(min(total_blocks, len(data) // EDID_BLOCK_SIZE)):
                self._blocks.append(bytearray(data[i * EDID_BLOCK_SIZE:(i + 1) * EDID_BLOCK_SIZE]))
            if not self._blocks:
                raise ValueError(f"EDID 数据至少需要 128 字节，实际只有 {len(data)} 字节")

        self._descriptors: List[DescriptorBlock] = []
        self._parse_descriptors()
        self._cea_ext: Optional[CEA861Extension] = None
        self._cea_ext2: Optional[CEA861Extension] = None  # Block 2
        self._ext2_tag: int = 0  # Block 2 的原始标签
        self._displayid_ext: Optional[DisplayIDExtension] = None  # Block 1 或 2 的 DisplayID
        self._parse_extensions()

    # ── 块管理 ──────────────────────────────────────────────────────────
    @property
    def block_count(self) -> int:
        """总块数"""
        return len(self._blocks)

    @property
    def total_size(self) -> int:
        """总字节数 (128/256/384)"""
        return len(self._blocks) * EDID_BLOCK_SIZE

    def get_block(self, index: int) -> bytearray:
        """获取指定块 (0=基块, 1/2=扩展块)"""
        if index < len(self._blocks):
            return self._blocks[index]
        raise IndexError(f"块 {index} 不存在 (共 {len(self._blocks)} 块)")

    def set_blocks(self, count: int):
        """调整总块数"""
        while len(self._blocks) < count:
            self._blocks.append(bytearray(EDID_BLOCK_SIZE))
        while len(self._blocks) > count:
            self._blocks.pop()
        self._blocks[0][126] = count - 1

    @property
    def base_data(self) -> bytearray:
        """基础 EDID 块 (Block 0, bytes 0-127)"""
        return self._blocks[0]

    # ── 扩展块解析 ──────────────────────────────────────────────────────
    def _parse_extensions(self):
        """解析所有扩展块 (Block 1 & Block 2)"""
        self._cea_ext = None
        self._cea_ext2 = None
        self._displayid_ext = None
        self._ext2_tag = 0
        for i in range(1, len(self._blocks)):
            tag = self._blocks[i][0]
            if tag == CEA_TAG:
                ext = CEA861Extension.from_bytes(bytes(self._blocks[i]))
                if ext:
                    if self._cea_ext is None:
                        self._cea_ext = ext
                    else:
                        self._cea_ext2 = ext
            elif tag == DISPLAYID_TAG:
                ext = DisplayIDExtension.from_bytes(bytes(self._blocks[i]))
                if ext:
                    self._displayid_ext = ext
            elif i == 2:
                self._ext2_tag = tag

    def _sync_extensions(self):
        """将扩展块序列化回底层数据"""
        # Block 1
        if self._cea_ext and len(self._blocks) >= 2:
            self._blocks[1] = bytearray(self._cea_ext.to_bytes())
        elif self._displayid_ext and len(self._blocks) >= 2:
            self._blocks[1] = bytearray(self._displayid_ext.to_bytes())
        elif (self._cea_ext or self._displayid_ext) and len(self._blocks) < 2:
            ext_data = self._cea_ext.to_bytes() if self._cea_ext else self._displayid_ext.to_bytes()
            self._blocks.append(bytearray(ext_data))
            self._blocks[0][126] = len(self._blocks) - 1

        # Block 2
        if self._cea_ext2 and len(self._blocks) >= 3:
            self._blocks[2] = bytearray(self._cea_ext2.to_bytes())
        elif self._displayid_ext and len(self._blocks) >= 3 and self._blocks[2][0] != DISPLAYID_TAG:
            self._blocks[2] = bytearray(self._displayid_ext.to_bytes())
        elif self._cea_ext2 and len(self._blocks) < 3:
            self._blocks.append(bytearray(self._cea_ext2.to_bytes()))
            self._blocks[0][126] = len(self._blocks) - 1

    @property
    def cea_extension(self) -> Optional[CEA861Extension]:
        """CEA-861 扩展块 (Block 1)"""
        return self._cea_ext

    @property
    def cea_extension2(self) -> Optional[CEA861Extension]:
        """CEA-861 扩展块 (Block 2)"""
        return self._cea_ext2

    @property
    def displayid_extension(self) -> Optional[DisplayIDExtension]:
        """DisplayID 扩展块"""
        return self._displayid_ext

    @property
    def ext2_info(self) -> dict:
        """Block 2 扩展块信息（含 CEA-861 或 DisplayID）"""
        if len(self._blocks) < 3:
            return {'exists': False}
        tag = self._blocks[2][0]
        tag_names = {
            0x02: 'CEA-861 Extension',
            0x70: 'DisplayID Extension',
            0xF0: 'Block Map',
            0x50: 'Localized String Extension (多语言)',
            0x10: 'VTB-EXT (Video Timing Block)',
        }
        parsed = (self._cea_ext2 is not None) or (self._displayid_ext is not None and tag == DISPLAYID_TAG)
        return {
            'exists': True,
            'tag': tag,
            'tag_name': tag_names.get(tag, f'未知标签 (0x{tag:02X})'),
            'parsed': parsed,
            'ceadata': self._cea_ext2,
            'displayid': self._displayid_ext if tag == DISPLAYID_TAG else None,
            'raw': bytes(self._blocks[2]),
        }

    def ensure_cea(self) -> CEA861Extension:
        """确保存在 CEA-861 扩展块"""
        if self._cea_ext is None:
            self._cea_ext = CEA861Extension.create_minimal()
            if len(self._blocks) < 2:
                self._blocks.append(bytearray(EDID_BLOCK_SIZE))
            self._blocks[0][126] = len(self._blocks) - 1
            self._sync_extensions()
        return self._cea_ext

    def ensure_cea2(self) -> CEA861Extension:
        """确保 Block 2 存在 CEA-861 扩展"""
        if self._cea_ext2 is None:
            self._cea_ext2 = CEA861Extension.create_minimal()
            if len(self._blocks) < 3:
                self.set_blocks(3)
            self._ext2_tag = CEA_TAG
            self._sync_extensions()
        return self._cea_ext2

    @property
    def all_dtds(self) -> List[Tuple[int, DetailedTiming]]:
        """获取所有 DTD (基础 + CEA 扩展 + DisplayID) → [(block_index, timing), ...]"""
        dtds = []
        for b in self._descriptors:
            if b.is_timing and b.timing:
                dtds.append((0, b.timing))
        if self._cea_ext:
            for dt in self._cea_ext.dtds:
                dtds.append((1, dt))
        if self._cea_ext2:
            for dt in self._cea_ext2.dtds:
                dtds.append((2, dt))
        # DisplayID timings
        if self._displayid_ext:
            for db, dt in self._displayid_ext.timings:
                dtds.append((2 if len(self._blocks) > 2 else 1, dt))
        return dtds

    # ── 描述符解析 ──────────────────────────────────────────────────────
    def _parse_descriptors(self):
        self._descriptors = []
        for i in range(DESCRIPTOR_COUNT):
            offset = 54 + i * DESCRIPTOR_SIZE
            self._descriptors.append(
                DescriptorBlock.from_bytes(bytes(self._blocks[0][offset:offset + DESCRIPTOR_SIZE]), i)
            )

    def _sync_descriptors(self):
        for i, block in enumerate(self._descriptors):
            offset = 54 + i * DESCRIPTOR_SIZE
            self._blocks[0][offset:offset + DESCRIPTOR_SIZE] = block.to_bytes()

    # ── 基础属性 ────────────────────────────────────────────────────────
    @property
    def is_valid_header(self) -> bool:
        return bytes(self._blocks[0][0:8]) == EDID_HEADER

    # --- 制造商 PnP ID ---
    @property
    def manufacturer_id(self) -> str:
        b8, b9 = self._blocks[0][8], self._blocks[0][9]
        l1, l2 = (b8 >> 2) & 0x1F, ((b8 & 0x03) << 3) | ((b9 >> 5) & 0x07)
        l3 = b9 & 0x1F
        try:
            return chr(l1 + ord('A') - 1) + chr(l2 + ord('A') - 1) + chr(l3 + ord('A') - 1)
        except ValueError:
            return "???"

    @manufacturer_id.setter
    def manufacturer_id(self, value: str):
        if len(value) != 3 or not value.isalpha():
            raise ValueError("制造商 ID 必须是恰好 3 个字母")
        value = value.upper()
        l1, l2, l3 = ord(value[0]) - 64, ord(value[1]) - 64, ord(value[2]) - 64
        self._blocks[0][8] = ((l1 & 0x1F) << 2) | ((l2 >> 3) & 0x03)
        self._blocks[0][9] = ((l2 & 0x07) << 5) | (l3 & 0x1F)

    # --- 产品代码 ---
    @property
    def product_code(self) -> int:
        return self._blocks[0][10] | (self._blocks[0][11] << 8)

    @product_code.setter
    def product_code(self, value: int):
        self._blocks[0][10], self._blocks[0][11] = value & 0xFF, (value >> 8) & 0xFF

    # --- 序列号 (4 字节) ---
    @property
    def serial_number(self) -> int:
        return (self._blocks[0][12] | (self._blocks[0][13] << 8) |
                (self._blocks[0][14] << 16) | (self._blocks[0][15] << 24))

    @serial_number.setter
    def serial_number(self, value: int):
        value &= 0xFFFFFFFF
        for i in range(4):
            self._blocks[0][12 + i] = (value >> (i * 8)) & 0xFF

    # --- 制造日期 ---
    @property
    def manufacture_week(self) -> int:
        return self._blocks[0][16]

    @manufacture_week.setter
    def manufacture_week(self, value: int):
        self._blocks[0][16] = max(0, min(value, 54))

    @property
    def manufacture_year(self) -> int:
        return self._blocks[0][17] + 1990

    @manufacture_year.setter
    def manufacture_year(self, value: int):
        self._blocks[0][17] = max(0, value - 1990)

    # --- EDID 版本 ---
    @property
    def edid_version(self) -> Tuple[int, int]:
        return (self._blocks[0][18], self._blocks[0][19])

    @edid_version.setter
    def edid_version(self, value: Tuple[int, int]):
        self._blocks[0][18], self._blocks[0][19] = value[0], value[1]

    # --- 输入类型 ---
    @property
    def is_digital(self) -> bool:
        return bool(self._blocks[0][20] & 0x80)

    @property
    def input_description(self) -> str:
        b = self._blocks[0][20]
        if b & 0x80:
            depth_map = {0: "未定义", 1: "6 bit", 2: "8 bit", 3: "10 bit",
                         4: "12 bit", 5: "14 bit", 6: "16 bit"}
            depth = depth_map.get((b >> 4) & 0x07, "保留")
            iface = "HDMI/a" if (b & 0x08) else ("DisplayPort" if (b & 0x04) else "DVI")
            return f"数字 ({iface}, {depth})"
        else:
            levels = (b & 0x60) >> 5
            video = "复合" if (b & 0x10) else "分离"
            return f"模拟 ({video}, 电平={levels})"

    # --- 屏幕尺寸 ---
    @property
    def screen_width_cm(self) -> int:
        return self._blocks[0][21]

    @screen_width_cm.setter
    def screen_width_cm(self, value: int):
        self._blocks[0][21] = value & 0xFF

    @property
    def screen_height_cm(self) -> int:
        return self._blocks[0][22]

    @screen_height_cm.setter
    def screen_height_cm(self, value: int):
        self._blocks[0][22] = value & 0xFF

    # --- 扩展块 ---
    @property
    def extension_count(self) -> int:
        return self._blocks[0][126]

    @extension_count.setter
    def extension_count(self, value: int):
        self._blocks[0][126] = value & 0xFF

    # ── 描述符操作 ──────────────────────────────────────────────────────
    @property
    def descriptors(self) -> List[DescriptorBlock]:
        return self._descriptors

    def get_descriptor_by_tag(self, tag: int) -> Optional[DescriptorBlock]:
        for b in self._descriptors:
            if not b.is_timing and b.monitor and b.monitor.tag == tag:
                return b
        return None

    def get_model_name(self) -> Optional[str]:
        block = self.get_descriptor_by_tag(0xFC)
        return block.monitor.get_text() if block and block.monitor else None

    def set_model_name(self, name: str):
        block = self.get_descriptor_by_tag(0xFC)
        if block and block.monitor:
            block.monitor.set_text(name)
        else:
            block = self._find_or_make_slot()
            block.is_timing = False
            block.monitor = MonitorDescriptor(tag=0xFC)
            block.monitor.set_text(name)
            block.timing = None
        self._sync_descriptors()

    def get_serial_string(self) -> Optional[str]:
        block = self.get_descriptor_by_tag(0xFF)
        return block.monitor.get_text() if block and block.monitor else None

    def set_serial_string(self, sn: str):
        block = self.get_descriptor_by_tag(0xFF)
        if block and block.monitor:
            block.monitor.set_text(sn)
        else:
            block = self._find_or_make_slot()
            block.is_timing = False
            block.monitor = MonitorDescriptor(tag=0xFF)
            block.monitor.set_text(sn)
            block.timing = None
        self._sync_descriptors()

    def _find_or_make_slot(self) -> DescriptorBlock:
        """寻找可用描述符槽位"""
        for b in self._descriptors:
            if not b.is_timing and b.monitor and b.monitor.tag == 0x10:
                return b
        for b in reversed(self._descriptors):
            if not b.is_timing and b.monitor and b.monitor.tag == 0 and b.monitor.data == bytes(13):
                return b
        key_tags = {0xFC, 0xFF, 0xFD}
        for b in reversed(self._descriptors):
            if not b.is_timing and b.monitor and b.monitor.tag not in key_tags:
                return b
        for b in reversed(self._descriptors):
            if not b.is_timing:
                return b
        return self._descriptors[-1]

    def add_detailed_timing(self, timing: DetailedTiming, index: Optional[int] = None):
        if index is not None and 0 <= index < DESCRIPTOR_COUNT:
            self._descriptors[index].is_timing = True
            self._descriptors[index].timing = timing
            self._descriptors[index].monitor = None
        else:
            for blk in self._descriptors:
                if not blk.is_timing:
                    if blk.monitor and blk.monitor.tag in (0xFC, 0xFF):
                        continue
                    blk.is_timing = True
                    blk.timing = timing
                    blk.monitor = None
                    self._sync_descriptors()
                    return
            for blk in self._descriptors:
                if not blk.is_timing and blk.monitor and blk.monitor.tag not in (0xFC, 0xFF):
                    blk.is_timing = True
                    blk.timing = timing
                    blk.monitor = None
                    self._sync_descriptors()
                    return
            self._descriptors[-1].is_timing = True
            self._descriptors[-1].timing = timing
            self._descriptors[-1].monitor = None
        self._sync_descriptors()

    def remove_descriptor(self, index: int):
        if 0 <= index < DESCRIPTOR_COUNT:
            self._descriptors[index] = DescriptorBlock.from_bytes(b'\x00' * 18, index)
            self._descriptors[index].set_as_dummy()
            self._sync_descriptors()

    def clear_descriptor(self, index: int):
        if 0 <= index < DESCRIPTOR_COUNT:
            self._descriptors[index] = DescriptorBlock.from_bytes(b'\x00' * 18, index)
            self._sync_descriptors()

    # ── 标准时序 ────────────────────────────────────────────────────────
    def get_standard_timings(self) -> List[Tuple[int, int, int]]:
        timings = []
        for i in range(8):
            offset = 38 + i * 2
            b0, b1 = self._blocks[0][offset], self._blocks[0][offset + 1]
            if b0 == 0x01 and b1 == 0x01:
                continue
            timings.append(((b0 + 31) * 8, (b1 >> 6) & 0x03, (b1 & 0x3F) + 60))
        return timings

    def set_standard_timing(self, index: int, h_active: int, aspect_ratio: int, refresh: int):
        if 0 <= index < 8:
            offset = 38 + index * 2
            self._blocks[0][offset] = max(0, (h_active // 8) - 31) & 0xFF
            self._blocks[0][offset + 1] = (((aspect_ratio & 0x03) << 6) | max(0, min(refresh - 60, 63))) & 0xFF

    def clear_standard_timing(self, index: int):
        if 0 <= index < 8:
            offset = 38 + index * 2
            self._blocks[0][offset] = 0x01
            self._blocks[0][offset + 1] = 0x01

    # ── 校验和 ──────────────────────────────────────────────────────────
    def calculate_checksum(self, block_index: int = 0) -> int:
        blk = self._blocks[block_index]
        return (256 - sum(blk[:127])) % 256

    def update_checksum(self, block_index: int = 0):
        self._blocks[block_index][127] = self.calculate_checksum(block_index)

    def update_all_checksums(self):
        for i in range(len(self._blocks)):
            self.update_checksum(i)

    def is_checksum_valid(self, block_index: int = 0) -> bool:
        return sum(self._blocks[block_index][:128]) % 256 == 0

    # ── 序列化 ──────────────────────────────────────────────────────────
    def to_bytes(self) -> bytes:
        """导出基础 EDID (128 字节)"""
        self._sync_descriptors()
        self.update_checksum(0)
        return bytes(self._blocks[0])

    def to_bytes_all(self) -> bytes:
        """导出完整 EDID (128/256/384 字节)"""
        self._sync_descriptors()
        self.update_checksum(0)
        if self._cea_ext:
            self._sync_extensions()
            self.update_checksum(1)
        if self._cea_ext2:
            self._sync_extensions()
            self.update_checksum(2)
        elif len(self._blocks) >= 3:
            self.update_checksum(2)
        self._blocks[0][126] = len(self._blocks) - 1
        result = bytearray()
        for blk in self._blocks:
            result.extend(blk)
        return bytes(result)

    @classmethod
    def from_file(cls, path: str) -> 'EDID':
        """从文件加载 EDID。自动检测是否为 RTD 固件文件并提取 EDID。"""
        with open(path, 'rb') as f:
            data = f.read()

        # 自动检测 EDID header
        header_pos = data.find(EDID_HEADER)
        if header_pos == -1:
            raise ValueError("未在文件中找到 EDID 数据 (缺少 00 FF FF FF FF FF FF 00 头部)")

        if header_pos != 0:
            # EDID 不在文件开头 — 可能是 RTD 固件，提取 EDID 部分
            print(f"[EDID] 在文件偏移 0x{header_pos:04X} 处发现 EDID 头部 (文件={len(data)} 字节)")

        edid_start = header_pos
        remaining = len(data) - edid_start

        # 检查有多少个完整块
        base_count = data[edid_start + 126] if remaining >= 128 else 0
        expected = 1 + base_count
        max_blocks = min(expected, remaining // EDID_BLOCK_SIZE, MAX_BLOCKS)

        if max_blocks < 1:
            raise ValueError("文件太小，无法包含完整的 EDID")

        edid = cls(data[edid_start:edid_start + max_blocks * EDID_BLOCK_SIZE], total_blocks=max_blocks)
        edid._source_offset = header_pos  # 记录在原始文件中的偏移
        return edid

    @classmethod
    def find_all_edids(cls, data: bytes) -> List[Tuple[int, 'EDID']]:
        """在数据中搜索所有 EDID 块 (可用于分析 RTD 固件)"""
        results = []
        pos = 0
        while True:
            pos = data.find(EDID_HEADER, pos)
            if pos == -1:
                break
            remaining = len(data) - pos
            if remaining >= EDID_BLOCK_SIZE:
                try:
                    edid = cls(data[pos:pos + min(remaining, EDID_BLOCK_SIZE * MAX_BLOCKS)])
                    results.append((pos, edid))
                except Exception:
                    pass
            pos += 1
        return results

    def save(self, path: str):
        """保存完整 EDID 到文件"""
        with open(path, 'wb') as f:
            f.write(self.to_bytes_all())

    def save_to_rtd(self, rtd_path: str, output_path: str, offset: Optional[int] = None):
        """将修改后的 EDID 写回 RTD 固件文件

        Args:
            rtd_path:   原始 RTD 固件文件路径
            output_path: 输出文件路径
            offset:     EDID 在固件中的偏移 (None=自动搜索)
        """
        with open(rtd_path, 'rb') as f:
            firmware = bytearray(f.read())

        if offset is None:
            offset = firmware.find(EDID_HEADER)
            if offset == -1:
                raise ValueError("无法在 RTD 文件中找到 EDID 头部")

        edid_bytes = self.to_bytes_all()
        if offset + len(edid_bytes) > len(firmware):
            raise ValueError(f"EDID ({len(edid_bytes)} 字节) 超出固件范围 (偏移={offset:#x}, 固件={len(firmware)} 字节)")

        firmware[offset:offset + len(edid_bytes)] = edid_bytes
        with open(output_path, 'wb') as f:
            f.write(firmware)

    @classmethod
    def create_blank(cls, blocks: int = 1) -> 'EDID':
        """创建空白 EDID 模板"""
        edid = cls(total_blocks=blocks)
        edid.manufacturer_id = "XXX"
        edid.product_code = 0x0001
        edid.serial_number = 0x00000001
        edid.manufacture_week = 1
        edid.manufacture_year = 2025
        edid._blocks[0][20] = 0x80  # 数字输入
        edid._blocks[0][21] = 52
        edid._blocks[0][22] = 29
        edid._blocks[0][23] = 0x78  # Gamma 2.2, DPMS
        edid._blocks[0][24] = 0x0E  # sRGB
        edid.update_checksum(0)
        return edid


# ═══════════════════════════════════════════════════════════════════════════
# RTD 固件文件识别
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class RTDFirmwareInfo:
    """RTD 固件文件信息"""
    path: str = ""
    file_size: int = 0
    chip_vendor: str = "Unknown"
    chip_model: str = "Unknown"
    edid_offsets: List[int] = field(default_factory=list)
    edids: List[EDID] = field(default_factory=list)


def analyze_rtd_file(path: str) -> RTDFirmwareInfo:
    """分析 RTD 固件文件，提取 EDID 信息

    RTD Scaler 芯片常见型号:
      RTD2556  - 主流 1080P/1440P 电竞显示器
      RTD2660  - 较早的 1080P 方案
      RTD2662  - HDMI+VGA 方案
      RTD2668  - 基本显示方案
      RTD27xx  - 4K 系列
      RTD2851  - 4K@60Hz
      RTD2892  - 4K@144Hz

    RTD 固件特征:
      - 通常以特定字节序列开头 (如 Realtek 签名)
      - EDID 可位于多个偏移处
      - 有时同一固件包含多台显示器的 EDID
    """
    info = RTDFirmwareInfo(path=path)

    try:
        with open(path, 'rb') as f:
            data = f.read()
    except Exception as e:
        raise IOError(f"无法读取文件 {path}: {e}")

    info.file_size = len(data)

    # 检测芯片型号 (通过固件签名)
    if data[:4] == b'\x00\x00\x00\x00':
        info.chip_vendor = "Realtek (疑似)"
    elif b'Realtek' in data[:0x100]:
        info.chip_vendor = "Realtek"

    # RTD 芯片型号识别
    chip_signatures = {
        b'RTD2556': "RTD2556",
        b'RTD2660': "RTD2660",
        b'RTD2662': "RTD2662",
        b'RTD2668': "RTD2668",
        b'RTD27': "RTD27xx",
        b'RTD28': "RTD28xx",
    }
    for sig, model in chip_signatures.items():
        if sig in data[:0x200]:
            info.chip_model = model
            break

    if info.chip_model == "Unknown" and info.file_size in [0x80000, 0x100000, 0x200000]:
        info.chip_model = f"RTD2xxx ({info.file_size // 1024}KB 固件)"

    # 搜索所有 EDID 位置
    for pos, edid in EDID.find_all_edids(data):
        if edid.is_valid_header:
            info.edid_offsets.append(pos)
            info.edids.append(edid)

    return info


def find_edid_in_file(path: str) -> Optional[EDID]:
    """在任意文件中搜索 EDID 并返回第一个有效的"""
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except Exception:
        return None

    pos = data.find(EDID_HEADER)
    if pos == -1:
        return None

    remaining = len(data) - pos
    max_bytes = min(remaining, EDID_BLOCK_SIZE * MAX_BLOCKS)
    try:
        return EDID(data[pos:pos + max_bytes])
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# 常用时序预设
# ═══════════════════════════════════════════════════════════════════════════
TIMING_PRESETS: List[Tuple[str, DetailedTiming]] = [
    # ── 1080p (CEA-861) ──
    ("1920×1080 @ 60Hz (CEA-861)", DetailedTiming(
        pixel_clock=14850, h_active=1920, h_blanking=280,
        v_active=1080, v_blanking=45,
        h_front_porch=88, h_sync=44, v_front_porch=4, v_sync=5,
        h_image_size=527, v_image_size=296,
    )),
    ("1920×1080 @ 120Hz", DetailedTiming(
        pixel_clock=29700, h_active=1920, h_blanking=280,
        v_active=1080, v_blanking=45,
        h_front_porch=88, h_sync=44, v_front_porch=4, v_sync=5,
        h_image_size=527, v_image_size=296,
    )),
    ("1920×1080 @ 144Hz", DetailedTiming(
        pixel_clock=34650, h_active=1920, h_blanking=160,
        v_active=1080, v_blanking=77,
        h_front_porch=48, h_sync=32, v_front_porch=3, v_sync=5,
        h_image_size=527, v_image_size=296,
    )),
    ("1920×1080 @ 165Hz", DetailedTiming(
        pixel_clock=40200, h_active=1920, h_blanking=160,
        v_active=1080, v_blanking=91,
        h_front_porch=48, h_sync=32, v_front_porch=3, v_sync=5,
        h_image_size=527, v_image_size=296,
    )),
    # ── 1440p ──
    ("2560×1440 @ 60Hz (CVT-RB)", DetailedTiming(
        pixel_clock=24150, h_active=2560, h_blanking=160,
        v_active=1440, v_blanking=41,
        h_front_porch=48, h_sync=32, v_front_porch=3, v_sync=5,
        h_image_size=597, v_image_size=336,
    )),
    ("2560×1440 @ 120Hz", DetailedTiming(
        pixel_clock=49825, h_active=2560, h_blanking=160,
        v_active=1440, v_blanking=85,
        h_front_porch=48, h_sync=32, v_front_porch=3, v_sync=5,
        h_image_size=597, v_image_size=336,
    )),
    ("2560×1440 @ 144Hz", DetailedTiming(
        pixel_clock=59850, h_active=2560, h_blanking=160,
        v_active=1440, v_blanking=85,
        h_front_porch=48, h_sync=32, v_front_porch=3, v_sync=5,
        h_image_size=597, v_image_size=336,
    )),
    # ── 4K ──
    ("3840×2160 @ 60Hz", DetailedTiming(
        pixel_clock=59400, h_active=3840, h_blanking=560,
        v_active=2160, v_blanking=90,
        h_front_porch=176, h_sync=88, v_front_porch=8, v_sync=10,
        h_image_size=697, v_image_size=392,
    )),
    ("3840×2160 @ 144Hz (DSC)", DetailedTiming(
        pixel_clock=130900, h_active=3840, h_blanking=160,
        v_active=2160, v_blanking=113,
        h_front_porch=48, h_sync=32, v_front_porch=3, v_sync=5,
        h_image_size=697, v_image_size=392,
    )),
    # ── 其他 ──
    ("1680×1050 @ 60Hz (CVT-RB)", DetailedTiming(
        pixel_clock=11900, h_active=1680, h_blanking=160,
        v_active=1050, v_blanking=39,
        h_front_porch=48, h_sync=32, v_front_porch=3, v_sync=5,
        h_image_size=474, v_image_size=296,
    )),
    ("1280×720 @ 60Hz (CVT-RB)", DetailedTiming(
        pixel_clock=6400, h_active=1280, h_blanking=160,
        v_active=720, v_blanking=33,
        h_front_porch=48, h_sync=32, v_front_porch=3, v_sync=5,
        h_image_size=527, v_image_size=296,
    )),
    ("1024×768 @ 60Hz", DetailedTiming(
        pixel_clock=6500, h_active=1024, h_blanking=320,
        v_active=768, v_blanking=38,
        h_front_porch=24, h_sync=136, v_front_porch=3, v_sync=6,
        h_image_size=300, v_image_size=225,
    )),
    ("800×600 @ 60Hz", DetailedTiming(
        pixel_clock=4000, h_active=800, h_blanking=256,
        v_active=600, v_blanking=28,
        h_front_porch=40, h_sync=128, v_front_porch=1, v_sync=4,
        h_image_size=300, v_image_size=225,
    )),
    ("640×480 @ 60Hz", DetailedTiming(
        pixel_clock=2517, h_active=640, h_blanking=160,
        v_active=480, v_blanking=45,
        h_front_porch=16, h_sync=96, v_front_porch=10, v_sync=2,
        h_image_size=300, v_image_size=225,
    )),
    # ── 宽屏 ──
    ("3440×1440 @ 60Hz", DetailedTiming(
        pixel_clock=31975, h_active=3440, h_blanking=160,
        v_active=1440, v_blanking=41,
        h_front_porch=48, h_sync=32, v_front_porch=3, v_sync=5,
        h_image_size=797, v_image_size=334,
    )),
    ("2560×1080 @ 60Hz", DetailedTiming(
        pixel_clock=18500, h_active=2560, h_blanking=160,
        v_active=1080, v_blanking=41,
        h_front_porch=48, h_sync=32, v_front_porch=3, v_sync=5,
        h_image_size=677, v_image_size=290,
    )),
]


# ═══════════════════════════════════════════════════════════════════════════
# 常见 ODM 厂商 PnP ID 参考
# ═══════════════════════════════════════════════════════════════════════════
KNOWN_VENDORS = {
    "DEL": "Dell", "ACR": "Acer", "HWP": "HP", "SAM": "Samsung",
    "LEN": "Lenovo", "SNY": "Sony", "VSC": "ViewSonic", "AOC": "AOC",
    "GSM": "LG", "BNQ": "BenQ", "PHL": "Philips", "MSI": "MSI",
    "ACI": "ASUS", "AUS": "ASUS", "GIG": "Gigabyte", "HKC": "HKC",
    "IVM": "Iiyama", "NEC": "NEC", "SHL": "Sharp", "TAT": "Tatung",
    "EPI": "Envision", "FUS": "Fujitsu", "MEI": "Panasonic",
    "MIT": "Mitsubishi", "PIO": "Pioneer", "SYN": "Synaps",
    "VIZ": "Vizio", "PRS": "Princeton", "CMN": "Chi Mei",
    "AUO": "AU Optronics", "BOE": "BOE Technology",
    "LGD": "LG Display", "SEC": "Samsung Electronics",
    "INX": "Innolux", "SHP": "Sharp", "TMX": "Tianma",
    "IVO": "IVO", "HSD": "HannStar", "CPT": "Chunghwa",
    "PAN": "Panasonic Display",
}
