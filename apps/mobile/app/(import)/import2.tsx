import { ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { PillButton } from '@/components/PillButton';
import { ProgressDots } from '@/components/ProgressDots';
import { EXPORT_STEPS } from '@/data/exportSteps';
import { border, color, font, fontSize, radius, space } from '@/theme/tokens';

export default function Import2Screen() {
  const router = useRouter();

  return (
    <View style={{ flex: 1, backgroundColor: color.cream }}>
      <ScrollView
        contentContainerStyle={{ paddingHorizontal: space.xl, paddingTop: 54, paddingBottom: space.xxl, gap: space.md }}
      >
        <ProgressDots step={2} />

        <Text
          style={{
            fontFamily: font.display,
            fontSize: fontSize.displayMd,
            lineHeight: 32,
            letterSpacing: -0.3,
            color: color.ink,
            textTransform: 'uppercase',
            marginTop: 4,
          }}
        >
          GRAB YOUR{'\n'}DATA FROM{'\n'}INSTAGRAM.
        </Text>

        <View style={{ gap: space.md, marginTop: space.xs }}>
          {EXPORT_STEPS.map((step) => (
            <View
              key={step.num}
              style={{
                flexDirection: 'row',
                gap: space.lg,
                backgroundColor: color.cream,
                borderWidth: border.standard,
                borderColor: color.ink,
                borderRadius: radius.md,
                padding: space.lg,
              }}
            >
              <Text style={{ fontFamily: font.display, fontSize: 22, color: color.ink }}>{step.num}</Text>
              <View style={{ flex: 1, gap: 4 }}>
                <Text style={{ fontFamily: font.display, fontSize: 15, color: color.ink, textTransform: 'uppercase' }}>
                  {step.title}
                </Text>
                <Text style={{ fontFamily: font.body, fontSize: 12.5, lineHeight: 18, color: 'rgba(21,23,15,0.75)' }}>
                  {step.desc}
                </Text>
              </View>
            </View>
          ))}
        </View>
      </ScrollView>

      <View style={{ paddingHorizontal: space.xl, paddingBottom: space.xxl, gap: space.md }}>
        <PillButton label="I've got my zip — next" variant="ink" shadowColor={color.gold} minHeight={48} fontSize={15} onPress={() => router.push('/(import)/import3')} />
        <Text
          onPress={() => router.replace('/(app)/home')}
          style={{ alignSelf: 'center', fontFamily: font.body, fontSize: 12.5, color: color.ink, textDecorationLine: 'underline' }}
        >
          I'll do this later
        </Text>
      </View>
    </View>
  );
}
