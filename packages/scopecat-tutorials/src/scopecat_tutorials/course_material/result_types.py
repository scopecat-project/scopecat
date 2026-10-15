"""本教学实验的复数 IQ 类型,计算与历史读取共用。"""

from typing import Annotated

import scopecat as sc

type IQ = Annotated[complex, sc.Unit("ratio")]
