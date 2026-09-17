import { TextInput, type TextInputProps } from 'react-native';
import { alpha, border, color, font, fontSize, radius, space } from '@/theme/tokens';

interface TextFieldProps extends TextInputProps {
  minHeight?: number;
}

export function TextField({ minHeight = 48, style, ...rest }: TextFieldProps) {
  return (
    <TextInput
      placeholderTextColor={alpha(color.ink, 0.5)}
      style={[
        {
          width: '100%',
          minHeight,
          borderRadius: radius.pill,
          borderWidth: border.hairline,
          borderColor: color.ink,
          backgroundColor: color.white,
          paddingHorizontal: space.xl,
          fontFamily: font.body,
          fontSize: fontSize.bodyMd,
          color: color.ink,
        },
        style,
      ]}
      {...rest}
    />
  );
}
