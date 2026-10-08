/*
 * Copyright 2023 Mark Fairchild.
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
 *   - added QuestInstances / QuestInstanceData parsing (UESP QUST ChangeForm "A1b" region),
 *   - accepted refID-typed case 0 that stock ReSaver threw on.
 * These changes affect READ/diagnostic parsing only and are used solely as an on-the-fly
 * classpath overlay in front of the user's own downloaded ReSaver.jar (never redistributed).
 * See tools/resaver-cli/analysis-overlay/NOTICE.md.
 */
package resaver.ess;

import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.Objects;
import java.util.Optional;
import resaver.Analysis;
import static resaver.ess.ChangeFlagConstantsQust.*;

/**
 * Describes a ChangeForm containing a QUST record.
 *
 * @author Mark Fairchild
 */
public class ChangeFormQust extends GeneralElement implements ChangeFormData {

    /**
     * Creates a new <code>ChangeForm</code>.
     *
     * @param input The input stream.
     * @param flags The change form flags.
     * @param context The <code>ESSContext</code> info.
     * @throws ElementException
     * 
     */
    public ChangeFormQust(ByteBuffer input, Flags.Int flags, ESS.ESSContext context) throws ElementException {
        this(input, flags, false, context);
    }
    
    /**
     * Creates a new <code>ChangeForm</code>.
     *
     * @param input The input stream.
     * @param flags The change form flags.
     * @param context The <code>ESSContext</code> info.
     * @param inline Indicates that the changeform appears as an element
     * of another changeform so unparsed data in <code>input</code> should be 
     * ignored.
     * @throws ElementException
     * 
     */
    public ChangeFormQust(ByteBuffer input, Flags.Int flags, boolean inline, ESS.ESSContext context) throws ElementException {
        Objects.requireNonNull(input);

        if (flags.getFlag(CHANGE_FORM_FLAGS)) {
            this.CHANGEFORMFLAGS = super.readElement(input, CHANGE_FORM_FLAGS, in -> new ChangeFormFlags(in));
        } else {
            this.CHANGEFORMFLAGS = null;
        }

        try {
            this.QUEST_FLAGS = flags.getFlag(CHANGE_QUEST_FLAGS) 
                    ? super.readElement(input, "QUEST_FLAGS", Flags::readShortFlags)
                    : null;

            this.SCRIPT_DELAY = flags.getFlag(CHANGE_QUEST_SCRIPT_DELAY)
                    ? super.readFloat(input, "SCRIPT_DELAY")
                    : Float.NaN;
            
            this.QUEST_STAGES = flags.getFlag(CHANGE_QUEST_STAGES)
                    ? (Element[]) super.readVSElemArray(input, "QUEST_STAGES", i -> new QuestStage(i, context))
                    : null;
                
            this.QUEST_OBJECTIVES = flags.getFlag(CHANGE_QUEST_OBJECTIVES)
                    ? (Element[]) super.readVSElemArray(input, "QUEST_OBJECTIVES", i -> new QuestObjective(i, context))
                    : null;
            
            this.QUEST_RUN_DATA = flags.getFlag(CHANGE_QUEST_RUNDATA)
                    ? new QuestRunData(input, context)
                    : null;

            // A1b: QuestInstances sits between RUNDATA and ALREADY_RUN (UESP Save File Format/QUST
            // Changeform). Stock ReSaver never reads CHANGE_QUEST_INSTANCES(27), silently under-reading
            // every instanced quest. Validated byte-exact (position==limit) across 3 saves.
            this.QUEST_INSTANCES = flags.getFlag(CHANGE_QUEST_INSTANCES)
                    ? new QuestInstances(input, context)
                    : null;

            this.ALREADY_RUN = flags.getFlag(CHANGE_QUEST_ALREADY_RUN)
                    ? super.readByte(input, "ALREADY_RUN")
                    : 0;

            // A1b: self-checking byte-exact gate (mirrors REFR/ACHR). Skipped when inline (this
            // changeform is nested inside another, where trailing data belongs to the parent).
            if (!inline && super.readUnparsed(input)) {
                throw new UnparsedException();
            }

        } catch (UnparsedException ex) {
            throw new ElementException("Unparsed data in QUST", ex, this);            
        } catch (RuntimeException ex) {
            super.readUnparsed(input);
            throw new ElementException("Error reading QUST", ex, this);
        }
    }

    /**
     * @return The <code>ChangeFormFlags</code> field.
     */
    public ChangeFormFlags getChangeFormFlags() {
        return this.CHANGEFORMFLAGS;
    }

    /**
     * @return String representation.
     */
    @Override
    public String toString() {
        return super.hasVal("FULLNAME") ? super.getVal("FULLNAME").toString() : "";
    }

    /**
     * @see ChangeFormData#getChangeConstants() 
     * @return 
     */
    @Override
    public ChangeFlagConstants[] getChangeConstants() {
        return ChangeFlagConstantsNPC.values();
    }
    
    /**
     * @see AnalyzableElement#getInfo(resaver.Analysis, resaver.ess.ESS)
     * @param analysis
     * @param save
     * @return
     */
    @Override
    public String getInfo(Optional<resaver.Analysis> analysis, ESS save) {
        //final StringBuilder BUILDER = new StringBuilder();
        //return BUILDER.toString();

        return super.toString();
    }

    /**
     * @see AnalyzableElement#matches(resaver.Analysis, resaver.Mod)
     * @param analysis
     * @param mod
     * @return
     */
    @Override
    public boolean matches(Optional<Analysis> analysis, String mod) {
        return false;
    }

    final private ChangeFormFlags CHANGEFORMFLAGS;
    final private Flags.Short QUEST_FLAGS;
    final private float SCRIPT_DELAY;
    final private Element[] QUEST_STAGES;
    final private Element[] QUEST_OBJECTIVES;
    final private QuestRunData QUEST_RUN_DATA;
    final private QuestInstances QUEST_INSTANCES;

    final private byte ALREADY_RUN;
    
    static private class QuestStage extends GeneralElement {

        public QuestStage(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            this.STAGE = super.readShort(input, "STAGE");
            this.STATUS = super.readElement(input, "STATUS", Flags::readByteFlags);
        }

        @Override
        public String toString() {
            return String.format("Stage %d with flag %s", STAGE, STATUS);
        }
        
        public short STAGE;
        public Flags STATUS;
    }

    static private class QuestObjective extends GeneralElement {

        public QuestObjective(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            this.UNK1 = super.readInt(input, "UNK1");
            this.UNK2 = super.readInt(input, "UNK2");
        }

        @Override
        public String toString() {
            return String.format("Objects %d:%d", UNK1, UNK2);
        }
        
        public int UNK1;
        public int UNK2;
    }
    

    // ---- A1b: QuestInstances / QuestInstanceData (UESP Save File Format/QUST Changeform) ----
    // CHANGE_QUEST_INSTANCES(27) data; written for quests with live alias instances.
    static private class QuestInstances extends GeneralElement {

        public QuestInstances(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            super.readInt(input, "UNK");
            super.readVSElemArray(input, "INSTANCE_DATA", i -> new QuestInstanceData(i, context));
        }
    }

    static private class QuestInstanceData extends GeneralElement {

        public QuestInstanceData(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            super.readInt(input, "UNK");
            super.readVSElemArray(input, "STRUCT1", i -> new QuestInstanceStruct1(i, context));   // {uint32; refID}
            super.readVSElemArray(input, "STRUCT2", i -> new QuestInstanceStruct2(i, context));   // {refID; uint32}
            super.readShort(input, "UNK16");
            super.readByte(input, "UNK8");
        }
    }

    static private class QuestInstanceStruct1 extends GeneralElement {

        public QuestInstanceStruct1(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            super.readInt(input, "UNK");
            super.readRefID(input, "REF", context);
        }
    }

    static private class QuestInstanceStruct2 extends GeneralElement {

        public QuestInstanceStruct2(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            super.readRefID(input, "REF", context);
            super.readInt(input, "UNK");
        }
    }

    static private class QuestRunData extends GeneralElement {

        public QuestRunData(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            UNK = super.readByte(input, "UNK");
            COUNT1 = super.readInt(input, "COUNT1");
            ITEMS1 = (Element[]) super.readElements(input, "ITEMS1", COUNT1, i -> new QuestRunDataItem1(i, context));
            COUNT2 = super.readInt(input, "COUNT2");
            ITEMS2 = (Element[]) super.readElements(input, "ITEMS2", COUNT2, i -> new QuestRunDataItem2(i, context));
            FLAG = super.readElement(input, "FLAG", Flags::readByteFlags);
            ITEM3 = FLAG.allZero() ? null : new QuestRunDataItem3(input, context);
        }
        
        final public byte UNK;
        final public int COUNT1;
        final public Element[] ITEMS1;
        final public int COUNT2;
        final public Element[] ITEMS2;
        final public Flags.Byte FLAG;
        final public QuestRunDataItem3 ITEM3;
    }
    
    static private class QuestRunDataItem1 extends GeneralElement {

        public QuestRunDataItem1(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            UNK = super.readInt(input, "UNK");
            FLAGS = super.readElement(input, "FLAG", Flags::readByteFlags);
            REFS = (Element[]) super.readElements(input, "REFS", (FLAGS.allZero() ? 1 : 5), context::readRefID);
        }
        
        @Override
        public String toString() {
            return String.format("RunDataItem1 %d [%s] %s", UNK, FLAGS, Arrays.toString(REFS));
        }
        
        final public int UNK;
        final public Flags.Byte FLAGS;
        final public Element[] REFS;
    }
    
    static private class QuestRunDataItem2 extends GeneralElement {

        public QuestRunDataItem2(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            super.readInt(input, "UNK");
            super.readRefID(input, "REF", context);
        }
    }
    
    static private class QuestRunDataItem3 extends GeneralElement {

        public QuestRunDataItem3(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            super.readInt(input, "UNK1");
            super.readFloat(input, "UNK2");
            int count = super.readInt(input, "COUNT");
            super.readElements(input, "ITEMS", count, i -> new QuestRunDataItem3Data(i, context));
        }
    }
    
    static private class QuestRunDataItem3Data extends GeneralElement {

        public QuestRunDataItem3Data(ByteBuffer input, ESS.ESSContext context) throws ElementException{
            int type = super.readInt(input, "TYPE");
            switch (type) {
                case 0:   // A1b: type 0 is valid and refID-typed (like 1/2/4); stock ReSaver threw on it.
                case 1:
                case 2:
                case 4:
                    super.readRefID(input, "UNK_REF", context);
                    break;
                case 3:
                    super.readInt(input, "UNK_INT");
                    break;
                default:
                    throw new IllegalStateException("Not expecting type " + type);
            }
        }
    }
    
}
