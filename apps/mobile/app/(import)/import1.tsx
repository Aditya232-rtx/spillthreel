import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import Import1Illustration from '../../assets/images/import1-illustration.svg';
import { PillButton } from '@/components/PillButton';
import { ProgressDots } from '@/components/ProgressDots';
import { color, font, fontSize, space } from '@/theme/tokens';

export default function Import1Screen() {
  const router = useRouter();

  return (
    <View style={{ flex: 1, backgroundColor: color.cream, paddingHorizontal: 28, paddingTop: 54, paddingBottom: 40 }}>
      <ProgressDots step={1} />

      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.lg }}>
        <Import1Illustration width={190} height={190} />
        <Text
          style={{
            fontFamily: font.display,
            fontSize: fontSize.displayMd,
            lineHeight: 32,
            letterSpacing: -0.3,
            color: color.ink,
            textAlign: 'center',
            textTransform: 'uppercase',
          }}
        >
          BRING IN THE{'\n'}REELS YOU{'\n'}ALREADY SAVED.
        </Text>
        <Text
          style={{
            fontFamily: font.displayMedium,
            fontSize: fontSize.bodyMd,
            lineHeight: 20,
            color: 'rgba(21,23,15,0.78)',
            textAlign: 'center',
          }}
        >
          Instagram lets you export your saved reels. Feed that file into SpillTheReel and every saved reel
          becomes searchable, with the caption, creator, and hashtags, in under a minute.
        </Text>
      </View>

      <PillButton label="Show me how" variant="coral" minHeight={48} fontSize={15} onPress={() => router.push('/(import)/import2')} />
      <Text
        onPress={() => router.replace('/(app)/home')}
        style={{
          textAlign: 'center',
          marginTop: space.md,
          fontFamily: font.body,
          fontSize: 12.5,
          color: color.ink,
          textDecorationLine: 'underline',
        }}
      >
        Maybe later
      </Text>
      <Text
        style={{
          textAlign: 'center',
          marginTop: space.sm,
          fontFamily: font.body,
          fontSize: fontSize.mono,
          letterSpacing: 0.6,
          color: 'rgba(21,23,15,0.55)',
        }}
      >
        YOU CAN ALWAYS IMPORT FROM SETTINGS LATER.
      </Text>
    </View>
  );
}
