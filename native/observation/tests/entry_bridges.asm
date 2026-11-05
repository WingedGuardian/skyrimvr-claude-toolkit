OPTION CASEMAP:NONE
EXTERN EntryBridgeOriginal:QWORD
INCLUDE bridge_common.inc
.code
BRIDGE EntryBridgeWrapper,EntryBridgeOriginal,0
END
