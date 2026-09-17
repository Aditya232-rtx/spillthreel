import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import * as DocumentPicker from 'expo-document-picker';
import { Svg, Path, Circle } from 'react-native-svg';
import { PillButton, type PillButtonVariant } from '@/components/PillButton';
import { ProgressDots } from '@/components/ProgressDots';
import { color, font, fontSize, radius, space } from '@/theme/tokens';

type UploadState = 'idle' | 'picked' | 'importing' | 'done';

const IMPORT_SIMULATED_DELAY_MS = 1800;

const COPY: Record<UploadState, { dropzone: string; dropzoneSub: string; buttonLabel: string; buttonVariant: PillButtonVariant }> = {
  idle: {
    dropzone: 'TAP TO PICK YOUR ZIP FILE',
    dropzoneSub: 'or choose it from Files',
    buttonLabel: 'Import now',
    buttonVariant: 'outline',
  },
  picked: {
    dropzone: 'ZIP SELECTED ✓',
    dropzoneSub: 'Ready when you are',
    buttonLabel: 'Import now',
    buttonVariant: 'coral',
  },
  importing: {
    dropzone: 'ZIP SELECTED ✓',
    dropzoneSub: 'Reading your saved reels…',
    buttonLabel: 'Importing…',
    buttonVariant: 'coral',
  },
  done: {
    dropzone: 'ZIP SELECTED ✓',
    dropzoneSub: 'All done',
    buttonLabel: "You're all set →",
    buttonVariant: 'ink',
  },
};

export default function Import3Screen() {
  const router = useRouter();
  const [state, setState] = useState<UploadState>('idle');
  const copy = COPY[state];

  async function pickFile() {
    if (state === 'importing') return;
    const result = await DocumentPicker.getDocumentAsync({ type: 'application/zip' });
    if (!result.canceled) {
      setState('picked');
    }
  }

  function handleImport() {
    if (state === 'idle') return;
    if (state === 'done') {
      router.replace('/(app)/home');
      return;
    }
    if (state === 'picked') {
      setState('importing');
      setTimeout(() => setState('done'), IMPORT_SIMULATED_DELAY_MS);
    }
  }

  return (
    <View style={{ flex: 1, backgroundColor: color.cream, paddingHorizontal: space.xl, paddingTop: 54, paddingBottom: 40, gap: space.xl }}>
      <ProgressDots step={3} />

      <Text
        style={{
          fontFamily: font.display,
          fontSize: fontSize.displayMd,
          lineHeight: 32,
          letterSpacing: -0.3,
          color: color.ink,
          textTransform: 'uppercase',
        }}
      >
        DROP THE ZIP{'\n'}HERE.
      </Text>

      <View style={{ flex: 1, justifyContent: 'center', gap: space.xl }}>
        <Pressable
          onPress={pickFile}
          style={{
            minHeight: 220,
            borderRadius: radius.xxl,
            borderWidth: 2,
            borderStyle: 'dashed',
            borderColor: color.ink,
            backgroundColor: color.cream,
            alignItems: 'center',
            justifyContent: 'center',
            gap: 10,
            padding: space.xl,
          }}
        >
          <Svg viewBox="0 0 260 280" width={42} height={45}>
            <Path
              d="M 60 50 L 190 112 L 155 129 C 172 155, 172 198, 142 220 C 122 234, 100 223, 94 200 C 90 175, 102 158, 110 151 L 60 175 Z"
              fill={color.ink}
              stroke={color.ink}
              strokeWidth={9}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            <Circle cx={212} cy={232} r={17} fill={color.ink} stroke={color.ink} strokeWidth={9} />
          </Svg>
          <Text style={{ fontFamily: font.display, fontSize: 15, color: color.ink, textAlign: 'center', textTransform: 'uppercase' }}>
            {copy.dropzone}
          </Text>
          <Text style={{ fontFamily: font.body, fontSize: 12.5, color: 'rgba(21,23,15,0.65)' }}>{copy.dropzoneSub}</Text>
        </Pressable>

        <View style={{ alignItems: 'center', gap: space.sm }}>
          <View
            style={{
              width: 16,
              height: 16,
              borderRadius: radius.pill,
              borderWidth: 1.5,
              borderColor: 'rgba(21,23,15,0.6)',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Text style={{ fontFamily: font.body, fontSize: 10, color: 'rgba(21,23,15,0.6)' }}>i</Text>
          </View>
          <Text
            style={{
              fontFamily: font.body,
              fontSize: fontSize.mono,
              lineHeight: 14,
              letterSpacing: 0.4,
              color: 'rgba(21,23,15,0.6)',
              textAlign: 'center',
            }}
          >
            WE READ YOUR FILE ON OUR SERVERS, THEN DELETE IT. YOUR ORIGINAL MEDIA NEVER LEAVES INSTAGRAM.
          </Text>
        </View>
      </View>

      <PillButton label={copy.buttonLabel} variant={copy.buttonVariant} minHeight={48} fontSize={15} disabled={state === 'idle' || state === 'importing'} onPress={handleImport} />
      <Text
        onPress={() => router.replace('/(app)/home')}
        style={{ alignSelf: 'center', fontFamily: font.body, fontSize: fontSize.mono, color: color.ink, textDecorationLine: 'underline' }}
      >
        Skip forever
      </Text>
    </View>
  );
}
