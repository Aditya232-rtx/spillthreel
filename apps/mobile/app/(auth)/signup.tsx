import { useState } from 'react';
import { Image, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { AmbientDot } from '@/components/AmbientDot';
import { Badge } from '@/components/Badge';
import { PillButton } from '@/components/PillButton';
import { TextField } from '@/components/TextField';
import { color, font, fontSize, space } from '@/theme/tokens';

const LOGO_SOURCE = require('../../assets/images/logo-mark-transparent.png');

export default function SignupScreen() {
  const router = useRouter();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  function handleContinue() {
    router.push('/(import)/import1');
  }

  return (
    <View style={{ flex: 1, backgroundColor: color.cream }}>
      <AmbientDot size={14} backgroundColor={color.violet} opacity={0.35} style={{ top: 80, right: 28 }} />
      <AmbientDot size={10} backgroundColor={color.gold} opacity={0.35} style={{ top: 170, left: 30 }} />
      <AmbientDot size={12} backgroundColor={color.coral} opacity={0.3} style={{ bottom: 100, right: 34 }} />

      <ScrollView
        contentContainerStyle={{
          flexGrow: 1,
          alignItems: 'center',
          justifyContent: 'center',
          paddingHorizontal: space.xxl,
          paddingTop: 40,
          paddingBottom: 24,
          gap: space.sm,
        }}
      >
        <Badge label="20 SEC ✦ FREE" rotateDeg={4} background={color.gold} style={{ marginBottom: 2 }} />
        <Image source={LOGO_SOURCE} style={{ width: 60, height: 60 }} resizeMode="contain" />

        <Text
          style={{
            fontFamily: font.display,
            fontSize: fontSize.displaySm + 6,
            lineHeight: 30,
            color: color.ink,
            textTransform: 'uppercase',
            textAlign: 'center',
          }}
        >
          Find stuff easily
        </Text>
        <Text style={{ fontFamily: font.displayMedium, fontSize: 13, color: 'rgba(21,23,15,0.65)', textAlign: 'center' }}>
          Takes 20 seconds. No credit card.
        </Text>

        <View style={{ width: '100%', gap: space.sm - 1, marginTop: space.xs }}>
          <PillButton label="Continue with Google" variant="outline" minHeight={46} fontSize={13.5} onPress={handleContinue} />
          <PillButton label="Continue with Apple" variant="outline" minHeight={46} fontSize={13.5} onPress={handleContinue} />

          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, marginVertical: space.xs }}>
            <View style={{ flex: 1, height: 1.5, backgroundColor: color.ink, opacity: 0.2 }} />
            <Text style={{ fontFamily: font.body, fontSize: fontSize.mono, color: 'rgba(21,23,15,0.5)' }}>OR</Text>
            <View style={{ flex: 1, height: 1.5, backgroundColor: color.ink, opacity: 0.2 }} />
          </View>

          <TextField placeholder="Your name" value={name} onChangeText={setName} minHeight={46} />
          <TextField placeholder="you@email.com" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" minHeight={46} />
          <TextField placeholder="8+ characters" value={password} onChangeText={setPassword} secureTextEntry minHeight={46} />

          <View style={{ marginTop: space.xs }}>
            <PillButton label="Create account" variant="coral" minHeight={50} fontSize={15} shadowColor={color.ink} onPress={handleContinue} />
          </View>
        </View>

        <Text style={{ fontFamily: font.body, fontSize: 11, color: 'rgba(21,23,15,0.6)', textAlign: 'center', marginTop: 4 }}>
          ALREADY HAVE AN ACCOUNT?{' '}
          <Text
            onPress={() => router.push('/(auth)/login')}
            style={{ textDecorationLine: 'underline', fontWeight: '600', color: color.ink }}
          >
            LOG IN.
          </Text>
        </Text>
      </ScrollView>
    </View>
  );
}
