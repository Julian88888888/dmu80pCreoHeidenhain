"""Профіль DMU 80P / Heidenhain TNC 426/430."""

MACHINE_DMU80P = {
    "id": "DMU80P",
    "name": "DMU 80P",
    "control": "TNC 426/430",
    "verticalHeadM": "M54 ;vertical head",
    "horizontalHeadM": "M53 ;horizontal head",
    "spindleOrientM": "M126",
    "machineCoordM": "M91",
    "planeComment": "PLOSKOST OBRABOTKI",
    "q339": 3,
    "coolantOn": "M3 M8",
    "coolantOff": "M5 M9",
    "endToolCall": "TOOL CALL Z S1000",
    "endM": "M1",
    "lastEndM": "M30",
    "pgmMaxLen": 8,
    "numberBlocks": False,
    "toolDr": "+0.0",
    "toolDl": "+0.0",
    "m53DatumY": 0.38,
    "m53DatumZ": -0.385,
}
