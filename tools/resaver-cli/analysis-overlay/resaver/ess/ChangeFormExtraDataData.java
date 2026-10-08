/*
 * Copyright 2017 Mark Fairchild.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
/*
 * MODIFICATION NOTICE (Apache-2.0 §4b) — this file was modified from the original ReSaver
 * (FallrimTools) source by the Skyrim AI Assistant Toolkit project (2026):
 *   - added parse coverage for ChangeForm extra-data types 16 and 135,
 *   - added a RECENT predecessor-type tracker used by the read-only `recon`/`extradata-scan`
 *     diagnostics (the "phantom test").
 * These changes affect READ/diagnostic parsing only and are used solely as an on-the-fly
 * classpath overlay in front of the user's own downloaded ReSaver.jar (never redistributed).
 * See tools/resaver-cli/analysis-overlay/NOTICE.md.
 */
package resaver.ess;

import java.nio.ByteBuffer;
import java.util.Objects;

/**
 * Manages the data in one element of a change form's extra data.
 *
 * @author Mark Fairchild
 */
public class ChangeFormExtraDataData extends GeneralElement {

    /**
     * Creates a new <code>ChangeFormInitialData</code>.
     *
     * @param input
     * @param context The <code>ESSContext</code> info.
     * @throws ElementException
     */
    public static final java.util.ArrayList<Integer> RECENT = new java.util.ArrayList<>();
    public ChangeFormExtraDataData(ByteBuffer input, ESS.ESSContext context) throws ElementException {
        Objects.requireNonNull(input);
        final int TYPE = Byte.toUnsignedInt(super.readByte(input, "TYPE"));
        RECENT.add(TYPE);
        if (TYPE < 0 || TYPE >= 256) {
            throw new IllegalArgumentException("Invalid extraData type: " + TYPE);
        }
        
        /*mf.BufferUtil.Peeker peeker;
        if (TYPE == 135) {
            int k = 0;
            peeker = new mf.BufferUtil.Peeker(input);
        }*/
        

        switch (TYPE) {
            case 0:
                this.NAME = "NULL";
                this.BRIEF = true;
                break;
            case 4:
                this.NAME = "ExtraExtraData1";
                this.BRIEF = true;
                super.readElement(input, "SUB_DATA", in -> new ChangeFormExtraDataData(in, context));
                break;
            case 8:
                this.NAME = "ExtraExtraData2";
                this.BRIEF = true;
                super.readElement(input, "SUB_DATA1", in -> new ChangeFormExtraDataData(in, context));
                super.readElement(input, "SUB_DATA2", in -> new ChangeFormExtraDataData(in, context));
                break;
            case 12:
                this.NAME = "ExtraExtraData3";
                this.BRIEF = true;
                super.readElement(input, "SUB_DATA1", in -> new ChangeFormExtraDataData(in, context));
                super.readElement(input, "SUB_DATA2", in -> new ChangeFormExtraDataData(in, context));
                super.readElement(input, "SUB_DATA3", in -> new ChangeFormExtraDataData(in, context));
                break;
            case 16:
                this.NAME = "ExtraExtraData4";
                this.BRIEF = true;
                super.readElement(input, "SUB_DATA1", in -> new ChangeFormExtraDataData(in, context));
                super.readElement(input, "SUB_DATA2", in -> new ChangeFormExtraDataData(in, context));
                super.readElement(input, "SUB_DATA3", in -> new ChangeFormExtraDataData(in, context));
                super.readElement(input, "SUB_DATA4", in -> new ChangeFormExtraDataData(in, context));
                break;
            case 20:
                this.NAME = "ExtraExtraData5";
                this.BRIEF = true;
                super.readElement(input, "SUB_DATA1", in -> new ChangeFormExtraDataData(in, context));
                super.readElement(input, "SUB_DATA2", in -> new ChangeFormExtraDataData(in, context));
                super.readElement(input, "SUB_DATA3", in -> new ChangeFormExtraDataData(in, context));
                super.readElement(input, "SUB_DATA4", in -> new ChangeFormExtraDataData(in, context));
                super.readElement(input, "SUB_DATA5", in -> new ChangeFormExtraDataData(in, context));
                break;
            case 22:
                this.NAME = "Worn";
                this.BRIEF = true;
                break;
            case 23:
                this.NAME = "WornLeft";
                this.BRIEF = true;
                break;
            case 24:
                this.NAME = "PackageStartLocation";
                this.BRIEF = true;
                super.readRefID(input, "UNK", context);
                super.readFloats(input, "POS", 3);
                super.readFloat(input, "UNK1");
                break;
            case 25:
                this.NAME = "Package";
                this.BRIEF = true;
                super.readRefID(input, "UNK1", context);
                super.readRefID(input, "UNK2", context);
                super.readInt(input, "UNK3");
                super.readBytes(input, "UNK4", 3);
                break;
            case 26:
                this.NAME = "TrespassPackage";
                this.BRIEF = true;
                RefID ref = super.readRefID(input, "PACK", context);
                if (ref.isZero()) {
                    throw new ElementException("TrespassPackage incomplete", this);
                }
                break;
            case 27:
                this.NAME = "RunOncePacks";
                this.BRIEF = true;
                super.readIntsVS(input, "PACKS");
                break;
            case 28:
                this.NAME = "ReferenceHandle";
                this.BRIEF = true;
                super.readRefID(input, "ID", context);
                break;
            case 29:
                this.NAME = "Unknown29";
                this.BRIEF = true;
                break;
            case 30:
                this.NAME = "LevCreaModifier";
                this.BRIEF = true;
                super.readInt(input, "MOD");
                break;
            case 31:
                this.NAME = "Ghost";
                this.BRIEF = true;
                super.readByte(input, "UNK");
                break;
            case 32:
                this.NAME = "UNKNOWN32";
                this.BRIEF = true;
                break;
            case 33:
                this.NAME = "Ownership";
                this.BRIEF = true;
                super.readRefID(input, "OWNER", context);
                break;
            case 34:
                this.NAME = "Global";
                this.BRIEF = true;
                super.readRefID(input, "UNK", context);
                break;
            case 35:
                this.NAME = "Rank";
                this.BRIEF = true;
                super.readRefID(input, "RANKID", context);
                break;
            case 36:
                this.NAME = "Count";
                this.BRIEF = true;
                super.readShort(input, "COUNT");
                break;
            case 37:
                this.NAME = "Health";
                this.BRIEF = true;
                super.readFloat(input, "HEALTH");
                break;
            case 39:
                this.NAME = "TimeLeft";
                this.BRIEF = true;
                super.readInt(input, "TIME");
                break;
            case 40:
                this.NAME = "Charge";
                this.BRIEF = true;
                super.readFloat(input, "CHARGE");
                break;
            case 42:
                this.NAME = "Lock";
                this.BRIEF = true;
                super.readBytes(input, "UNKS", 2);
                super.readRefID(input, "KEY", context);
                super.readInts(input, "UNKS2", 2);
                break;
            case 43:
                this.NAME = "Teleport";
                this.BRIEF = true;
                super.readFloats(input, "POS", 3);
                super.readFloats(input, "ROT", 3);
                super.readByte(input, "UNK");
                super.readRefID(input, "REF", context);
                break;
            case 44:
                this.NAME = "MapMarker";
                this.BRIEF = true;
                super.readByte(input, "UNK");
                break;
            case 45:
                this.NAME = "LeveledCreature";
                this.BRIEF = false;
                super.readRefID(input, "UNK1", context);
                super.readRefID(input, "UNK2", context);
                Flags.Int flags = super.readElement(input, "NPCChangeFlags", in -> Flags.readIntFlags(in));
                super.readElement(input, "NPC", in -> new ChangeFormNPC(in, flags, true, context));
                break;
            case 46:
                this.NAME = "LeveledItem";
                this.BRIEF = true;
                super.readInt(input, "UNK");
                super.readByte(input, "UNK2");
                break;
            case 47:
                this.NAME = "Scale";
                this.BRIEF = true;
                super.readFloat(input, "scale");
                break;
            case 49:
                this.NAME = "NonActorMagicCaster";
                this.BRIEF = false;
                super.readElement(input, "CASTER", in -> new NonActorMagicCaster(in, context));
                break;
            case 50:
                this.NAME = "NonActorMagicTarget";
                this.BRIEF = false;
                super.readRefID(input, "ref", context);
                super.readVSElemArray(input, "targets", in -> new MagicTarget(in, context));
                break;
            case 52:
                this.NAME = "PlayerCrimeList";
                this.BRIEF = false;
                super.readLongsVS(input, "list");
                break;
            case 53:
                this.NAME= "Unknown53";
                this.BRIEF = true;
                break;                
            case 56:
                this.NAME = "ItemDropper";
                this.BRIEF = true;
                super.readRefID(input, "unk", context);
                break;
            case 61:
                this.NAME = "CannotWear";
                this.BRIEF = true;
                break;
            case 62:
                this.NAME = "ExtraPoison";
                this.BRIEF = true;
                super.readRefID(input, "ref", context);
                super.readInt(input, "unk");
                break;
            case 68:
                this.NAME = "FriendHits";
                this.BRIEF = true;
                super.readFloatsVS(input, "unk");
                break;
            case 69:
                this.NAME = "HeadingTarget";
                this.BRIEF = true;
                super.readRefID(input, "targetID", context);
                break;
            case 72:
                this.NAME = "StartingWorldOrCell";
                this.BRIEF = true;
                super.readRefID(input, "worldOrCellID", context);
                break;
            case 73:
                this.NAME = "HotKey";
                this.BRIEF = true;
                super.readByte(input, "unk");
                break;
            case 76:
                this.NAME = "InfoGeneralTopic";
                this.BRIEF = true;
                super.readElement(input, "TOPIC", in -> new InfoGeneralTopic(in, context));
                break;
            case 77:
                this.NAME = "HasNoRumors";
                this.BRIEF = true;
                super.readByte(input, "FLAG");
                break;
            case 79:
                this.NAME = "TerminalState";
                this.BRIEF = true;
                super.readBytes(input, "STATE", 2);
                break;
            case 83:
                this.NAME = "Unknown83";
                this.BRIEF = true;
                super.readInt(input, "unk");
                break;
            case 84:
                this.NAME = "CanTalkToPlayer";
                this.BRIEF = true;
                super.readByte(input, "FLAG");
                break;
            case 85:
                this.NAME = "ObjectHealth";
                this.BRIEF = true;
                super.readFloat(input, "HEALTH");
                break;
            case 88:
                this.NAME = "ModelSwap";
                this.BRIEF = true;
                super.readRefID(input, "REF", context);
                super.readInt(input, "UNK");
                break;
            case 89:
                this.NAME = "Radius";
                this.BRIEF = true;
                super.readFloat(input, "RADIUS");
                break;
            case 91:
                this.NAME = "FactionChanges";
                this.BRIEF = true;
                super.readVSElemArray(input, NAME, in -> new FactionChange(in, context));
                super.readRefID(input, "FACTION2", context);
                super.readByte(input, "RANK2");
                break;
            case 92:
                this.NAME = "DismemberedLimbs";
                this.BRIEF = false;
                super.readElement(input, "DISMEMBERED", in -> new DismemberedLimbs(in, context));
                break;
            case 93:
                this.NAME = "ActorCause";
                this.BRIEF = true;
                super.readInt(input, "ID");
                break;
            case 101:
                this.NAME = "CombatStyle";
                this.BRIEF = true;
                super.readRefID(input, "REF", context);
                break;
            case 102:
                this.NAME = "MYSTERIOUS";
                this.BRIEF = false;
                super.readRefID(input, "REF1", context);
                super.readRefID(input, "REF2", context);
                break;
            case 104:
                this.NAME = "OpenCloseActivateRef";
                this.BRIEF = true;
                super.readRefID(input, "REF", context);
                break;                
            case 106:
                this.NAME = "Ammo";
                this.BRIEF = true;
                super.readRefID(input, "REF", context);
                super.readInt(input, "COUNT");
                break;   
            case 111:
                this.NAME = "SayTopicInfoOnceADay";
                this.BRIEF = false;
                super.readVSElemArray(input, "INFOS", in -> new SayTopicInfoOnceADay(in, context));
                break;
            case 112:
                this.NAME = "EncounterZone";
                this.BRIEF = true;
                super.readRefID(input, "REF", context);
                break;
            case 113:
                this.NAME = "SayToTopicInfo";
                this.BRIEF = false;
                super.readElement(input, "DATA", in -> new SayToTopicInfo(in, context));
                break;
            case 120: 
                this.NAME = "GuardedRefData";
                this.BRIEF = false;
                super.readVSElemArray(input, "DATA", in -> new GuardedRefData(in, context));
                break;
            case 133:
                this.NAME = "AshPileRef";
                this.BRIEF = true;
                super.readRefID(input, "REF", context);
                break;
            case 135:
                this.NAME = "FollowerSwimBreadcrumbs";
                this.BRIEF = false;
                super.readFloats(input, "UNK1", 3);
                super.readRefID(input, "REF", context);
                super.readInt(input, "UNK2");
                super.readVSElemArray(input, "BREADCRUMBS", in -> new FollowerSwimBreadcrumb(in, context));
                break;
            case 136:
                this.NAME = "AliasInstanceArray";
                this.BRIEF = false;
                super.readVSElemArray(input, "ALIASES", in -> new AliasInstance(in, context));
                break;
            case 140:
                this.NAME = "PromotedRef";
                this.BRIEF = true;
                super.readVSElemArray(input, "REFS", in -> context.readRefID(in));
                break;
            case 142:
                this.NAME = "OutfitItem";
                this.BRIEF = true;
                super.readRefID(input, "REF", context);
                break;
            case 146:
                this.NAME = "SceneData";
                this.BRIEF = true;
                super.readRefID(input, "REF", context);
                break;
            case 149:
                this.NAME = "FromAlias";
                this.BRIEF = true;
                super.readRefID(input, "REF", context);
                super.readInt(input, "VAL");
                break;
            case 150:
                this.NAME = "ShouldWear";
                this.BRIEF = true;
                super.readByte(input, "UNK");
                break;
            case 152:
                this.NAME = "AttachedArrows3D";
                this.BRIEF = false;
                super.readVSElemArray(input, "ARROWS", in -> new AttachedArrow(in, context));
                super.readShort(input, "UNK1");
                super.readShort(input, "UNK2");                
                break;
            case 153:
                this.NAME = "TextDisplayData";
                this.BRIEF = true;
                super.readElement(input, "TEXTDISPLAY", in -> new TextDisplayData(in, context));
                break;
            case 155:
                this.NAME = "Enchantment";
                this.BRIEF = true;
                super.readRefID(input, "ENCH", context);
                super.readShort(input, "CHARGE");
                break;
            case 156:
                this.NAME = "Soul";
                this.BRIEF = true;
                super.readByte(input, "SIZE");
                break;                    
            case 157:
                this.NAME = "ForcedTarget";
                this.BRIEF = true;
                super.readRefID(input, "TARGET", context);
                break;
            case 159:
                this.NAME = "UniqueId";
                this.BRIEF = true;
                super.readInt(input, "ID1");
                super.readShort(input, "ID2");
                break;
            case 160:
                this.NAME = "Flags";
                this.BRIEF = true;
                super.readElement(input, "FLAGS", in -> Flags.readIntFlags(in));
                break;
            case 161:
                this.NAME = "RefrPath";
                this.BRIEF = false;
                super.readFloats(input, "UNK1", 3*6);
                super.readInts(input, "UNK2", 4);
                break;
            case 164:
                this.NAME = "ForcedLandingMarker";
                this.BRIEF = true;
                super.readRefID(input, "SITE", context);
                break;
            case 169:
                this.NAME = "Interaction";
                this.BRIEF = false;
                super.readInt(input, "UNK1");
                super.readRefID(input, "Target1", context);
                super.readRefID(input, "Target2", context);
                super.readByte(input, "UNK2");
                break;
            case 174:
                this.NAME = "GroupConstraint";
                this.BRIEF = true;
                super.readElement(input, "CONSTRAINT", in -> new GroupConstraint(in, context));
                break;
            case 175:
                this.NAME = "ScriptedAnimDependence";
                this.BRIEF = true;
                super.read32ElemArray(input, "CONSTRAINT", in -> new ScriptedAnimDependence(in, context));
                break;
            case 176:
                this.NAME = "CachedScale";
                this.BRIEF = true;
                super.readFloat(input, "SCALE1");
                super.readFloat(input, "SCALE2");
                break;
            default:
                this.NAME = "UNKNOWN_EXTRA_TYPE";
                this.BRIEF = true;
                throw new ElementException("Unknown ExtraData: type=" + TYPE, null, this);
        }
    }

    @Override
    protected String toStringFlat(String name) {
        return super.toStringFlat(this.NAME);
    }

    @Override
    protected String toStringStructured(String name, int level) {
        return this.BRIEF 
                ? indent2(level) + super.toStringFlat(this.NAME)
                : super.toStringStructured(this.NAME, level); 
    }
    
    final public String NAME;
    final private boolean BRIEF;
    
    static class FollowerSwimBreadcrumb extends GeneralElement {
        FollowerSwimBreadcrumb(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readFloats(input, "UNK1", 3);
            super.readRefID(input, "REF1", context);
            super.readFloats(input, "UNK2", 3);
            super.readRefID(input, "REF2", context);
            super.readByte(input, "UNK3");
        }
    }

    static class AliasInstance extends GeneralElement {

        AliasInstance(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            this.QUEST = super.readRefID(input, "QUEST", context);
            this.ALIAS = super.readInt(input, "ALIAS");
        }
        
        @Override
        protected String toStringFlat(String name) {
            return new StringBuilder()
                    .append("Alias ")
                    .append(this.ALIAS)
                    .append(" of ")
                    .append(this.QUEST.toHTML(null))
                    .toString();
        }
        
        final private RefID QUEST;
        final private int ALIAS;
    }

    static class MagicTarget extends GeneralElement {

        MagicTarget(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readRefID(input, "REF", context);
            super.readByte(input, "unk1");
            super.readVSVal(input, "unk2");
            super.readBytesVS(input, "data");
        }
    }

    static class AttachedArrow extends GeneralElement {
        AttachedArrow(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            REF = super.readRefID(input, "REF", context);
            if (!REF.isZero()) {
                short unknU16 = super.readShort(input, "unknU16");
                
                if (unknU16 != -1) {
                    super.readInt(input, "UNK2");
                    super.readFloats(input, "LOCATION", 8);
                }
            }
        }
        
        final public RefID REF;
    }

    static class TextDisplayData extends GeneralElement {
        TextDisplayData(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            RefID ref1 = super.readRefID(input, "REF1", context);
            RefID ref2 = super.readRefID(input, "REF2", context);
            int UNK = super.readInt(input, "UNK");
            if (ref1.isZero() && ref2.isZero() && UNK == -2) {
                super.readWString(input, "TEXT");
            }
        }
    }

    static class DismemberedLimbs extends GeneralElement {
        DismemberedLimbs(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readShort(input, "UNK1");
            super.readInt(input, "UNK2");
            super.readInt(input, "UNK3");
            super.readByte(input, "UNK4");
            super.readRefID(input, "REF", context);
            super.readVSElemArray(input, "LIMBS", in -> new DismemberedLimb(in, context));
        }
    }
    static class DismemberedLimb extends GeneralElement {
        DismemberedLimb(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readBytes(input, "UNK", 4);
            super.readVSElemArray(input, "REFS", in -> context.readRefID(in));
        }
    }
    
    static class GroupConstraint extends GeneralElement {
        GroupConstraint(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readInt(input, "UNK1");
            super.readRefID(input, "REF", context);
            super.readWString(input, "NAME1");
            super.readWString(input, "NAME2");
            super.readFloats(input, "POS", 3);
            super.readFloats(input, "ROT", 3);
            super.readInt(input, "UNK2");
            super.readFloat(input, "UNK3");
        }
    }
    
    static class ScriptedAnimDependence extends GeneralElement {
        ScriptedAnimDependence(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readRefID(input, "REF", context);
            super.readInt(input, "UNK1");
        }
    }

    static class SayTopicInfoOnceADay extends GeneralElement {
        SayTopicInfoOnceADay(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readRefID(input, "INFO", context);
            super.readInt(input, "UNK1");
            super.readInt(input, "UNK2");
        }
    }
    
    static class SayToTopicInfo extends GeneralElement {
        SayToTopicInfo(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readRefID(input, "REF1", context);
            super.readByte(input, "UNK1");
            super.readInt(input, "UNK2");
            super.readRefID(input, "REF2", context);
            // B2b fix: type 113 carries a SayToTopicInfoData = vsval count + SayToTopicInfoData2[count]
            // + uint16 + refID[4] (UESP). Stock reads a single bare SayToTopicInfoData2, so the count
            // byte corrupts TEXT1's wstring length -> BufferUnderflow on every inline-text instance.
            super.readVSElemArray(input, "DATA2", in -> new SayToTopicInfoData2(in, context));
            super.readShort(input, "UNK_U16");
            super.readElements(input, "REFS", 4, in -> context.readRefID(in));
        }
    }
    
    static class SayToTopicInfoData2 extends GeneralElement {
        SayToTopicInfoData2(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readWString(input, "TEXT1");
            super.readWString(input, "TEXT2");
            super.readInt(input, "UNK1");
            super.readInt(input, "UNK2");
            super.readByte(input, "UNK3");
            super.readRefID(input, "REF1", context);
            super.readRefID(input, "REF2", context);
            super.readRefID(input, "REF3", context);
            super.readByte(input, "UNK3");            
        }
    }

    static class GuardedRefData extends GeneralElement {
        GuardedRefData(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readRefID(input, "INFO", context);
            super.readInt(input, "UNK1");
            super.readByte(input, "UNK2");
        }
    }
  
    static class FactionChange extends GeneralElement {
        FactionChange(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readRefID(input, "FACTION", context);
            super.readByte(input, "RANK");
        }
    }
  
    static class InfoGeneralTopic extends GeneralElement {
        InfoGeneralTopic(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readWString(input, "NAME");
            super.readBytes(input, "UNK", 5);
            super.readElements(input, "REFS", 4, in -> context.readRefID(in));
        }
    }
  
    static class NonActorMagicCaster extends GeneralElement {
        NonActorMagicCaster(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readInt(input, "unk1");
            super.readRefID(input, "ref1", context);
            super.readInt(input, "unk2");
            super.readInt(input, "unk3");
            super.readRefID(input, "ref2", context);
            super.readFloat(input, "unk4");
            super.readRefID(input, "ref3", context);
            super.readRefID(input, "ref4", context);
        }
    }
  
    static class Explosion extends GeneralElement {
        Explosion(ByteBuffer input, ESS.ESSContext context) throws ElementException {
            super.readFloats(input, "UNKS", 4);
            super.readInts(input, "UNK1", 2);
            super.readFloats(input, "UNK2", 2);
            super.readFloats(input, "UNK3", 3);
            super.readFloats(input, "UNK4", 3);           
            super.readFloat(input, "UNK5");
            
            super.readElements(input, "REFS", 4, in -> context.readRefID(in));
            super.readFloat(input, "UNK6");
            super.readInt(input, "UNK7");
            super.readByte(input, "FLAG");
            super.readElement(input, "CASTER", in -> new NonActorMagicCaster(in, context));

        }
    }

        
}
