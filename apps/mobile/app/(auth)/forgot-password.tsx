import { useState } from 'react';
import { Image, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { AmbientDot } from '@/components/AmbientDot';
import { Badge } from '@/components/Badge';
import { PillButton } from '@/components/PillButton';
import { TextField } from '@/components/TextField';
import { buildPasswordResetRedirectUrl } from '@/lib/oauth';
import { supabase } from '@/lib/supabase';
import { color, font, fontSize, space } from '@/theme/tokens';

const LOGO_SOURCE = require('../../assets/images/logo-mark-transparent.png');

export default function ForgotPasswordScreen() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function handleSendLink(): Promise<void> {
    if (!email.trim()) {
      setErrorMessage('Enter your email address');
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    const { error } = await supabase.auth.resetPasswordForEmail(email.trim(), {
      redirectTo: buildPasswordResetRedirectUrl(),
    });
    setSubmitting(false);
    if (error) {
      setErrorMessage(error.message);
      return;
    }
    setSent(true);
  }

  return (
    <View style={{ flex: 1, backgroundColor: color.cream }}>
      <AmbientDot size={16} backgroundColor={color.coral} opacity={0.35} style={{ top: 74, left: 30 }} />
      <AmbientDot size={10} backgroundColor={color.violet} opacity={0.3} style={{ top: 150, right: 34 }} />

      <ScrollView
        contentContainerStyle={{
          flexGrow: 1,
          alignItems: 'center',
          justifyContent: 'center',
          paddingHorizontal: space.xxl,
          paddingVertical: 44,
          gap: space.md,
        }}
      >
        <Badge label="NO WORRIES ✦" rotateDeg={-4} background={color.gold} style={{ marginBottom: space.xs }} />
        <Image source={LOGO_SOURCE} style={{ width: 66, height: 66 }} resizeMode="contain" />

        <Text
          style={{
            fontFamily: font.display,
            fontSize: fontSize.displayMd + 2,
            lineHeight: 34,
            color: color.ink,
            textTransform: 'uppercase',
            textAlign: 'center',
          }}
        >
          RESET PASSWORD.
        </Text>

        {sent ? (
          <>
            <Text style={{ fontFamily: font.displayMedium, fontSize: fontSize.bodyMd, color: 'rgba(21,23,15,0.65)', textAlign: 'center' }}>
              Check your inbox — we sent a reset link to {email.trim()}.
            </Text>
            <Text style={{ fontFamily: font.body, fontSize: 12, color: 'rgba(21,23,15,0.55)', textAlign: 'center' }}>
              The link expires soon and works once. Did not arrive? Check spam, then try again.
            </Text>
            <View style={{ width: '100%', marginTop: space.sm }}>
              <PillButton
                label="Back to log in"
                variant="ink"
                minHeight={50}
                fontSize={15}
                onPress={() => router.replace('/(auth)/login')}
              />
            </View>
          </>
        ) : (
          <>
            <Text style={{ fontFamily: font.displayMedium, fontSize: fontSize.bodyMd, color: 'rgba(21,23,15,0.65)', textAlign: 'center' }}>
              Enter your account email and we will send you a reset link.
            </Text>

            <View style={{ width: '100%', gap: space.sm, marginTop: space.sm }}>
              <TextField
                placeholder="you@email.com"
                value={email}
                onChangeText={setEmail}
                autoCapitalize="none"
                keyboardType="email-address"
              />

              {errorMessage ? (
                <Text style={{ fontFamily: font.body, fontSize: 12, color: color.coral, textAlign: 'center' }}>
                  {errorMessage}
                </Text>
              ) : null}

              <View style={{ marginTop: space.xs }}>
                <PillButton
                  label={submitting ? 'Sending…' : 'Send reset link'}
                  variant="coral"
                  minHeight={50}
                  fontSize={15}
                  shadowColor={color.ink}
                  disabled={submitting}
                  onPress={handleSendLink}
                />
              </View>
            </View>

            <Text
              onPress={() => router.back()}
              style={{ fontFamily: font.body, fontSize: 12, color: color.ink, textDecorationLine: 'underline', marginTop: space.sm }}
            >
              Back to log in
            </Text>
          </>
        )}
      </ScrollView>
    </View>
  );
}
