import { useState } from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import Animated, { FadeInUp } from 'react-native-reanimated';
import { Badge } from '@/components/Badge';
import { LogoBurst } from '@/components/LogoBurst';
import { PillButton } from '@/components/PillButton';
import { color, font, fontSize, space } from '@/theme/tokens';

const LOGO_SOURCE = require('../../assets/images/logo-mark-transparent.png');

export default function WelcomeScreen() {
  const router = useRouter();
  const [contentVisible, setContentVisible] = useState(false);

  return (
    <View style={{ flex: 1, backgroundColor: color.cream }}>
      <Badge
        label="AI-POWERED ✦"
        rotateDeg={8}
        background={color.gold}
        style={{ position: 'absolute', top: 60, right: 26 }}
      />

      <View
        style={{
          flex: 1,
          alignItems: 'center',
          justifyContent: 'center',
          paddingHorizontal: space.xxl,
          paddingTop: 54,
          paddingBottom: 40,
          gap: space.xl,
        }}
      >
        <LogoBurst size={180} logoSource={LOGO_SOURCE} onSettled={() => setContentVisible(true)} />

        {contentVisible ? (
          <Animated.View
            entering={FadeInUp.duration(550)}
            style={{ alignItems: 'center', gap: space.xl, width: '100%' }}
          >
            <Text style={{ fontFamily: font.script, fontSize: 19, color: color.coral }}>Spillthereel</Text>

            <Text
              style={{
                fontFamily: font.display,
                fontSize: fontSize.displayXl,
                lineHeight: 44,
                letterSpacing: -1,
                color: color.ink,
                textAlign: 'center',
                textTransform: 'uppercase',
              }}
            >
              STOP{'\n'}RE-SCROLLING.{'\n'}
              <Text style={{ color: color.coral }}>START ASKING.</Text>
            </Text>

            <Text
              style={{
                fontFamily: font.displaySemibold,
                fontSize: fontSize.bodyMd,
                lineHeight: 20,
                color: 'rgba(21,23,15,0.78)',
                textAlign: 'center',
                maxWidth: 250,
              }}
            >
              Save any reel. Get it back by just asking — recipes, workouts, that one travel tip.
            </Text>

            <PillButton label="Log in" variant="ink" shadowColor={color.gold} onPress={() => router.push('/(auth)/login')} />
            <PillButton label="Sign up" variant="coral" shadowColor={color.ink} onPress={() => router.push('/(auth)/signup')} />
          </Animated.View>
        ) : null}
      </View>

      <Text
        style={{
          position: 'absolute',
          bottom: 16,
          left: 0,
          right: 0,
          textAlign: 'center',
          fontFamily: font.body,
          fontSize: fontSize.monoXs,
          letterSpacing: 1,
          color: 'rgba(21,23,15,0.5)',
        }}
      >
        BY CONTINUING YOU AGREE TO OUR TERMS AND PRIVACY POLICY.
      </Text>
    </View>
  );
}
