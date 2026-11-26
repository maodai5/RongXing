// AI 思考中动画：三个依次跳动的圆点 + "绒星正在思考…"文案。
// 用于聊天页等待 AI 回复时显示在消息列表底部，提供可见的反馈。
import { useEffect, useMemo } from 'react';
import { Animated, Easing, Image, StyleSheet, Text, View } from 'react-native';
import { colors } from '../theme';

type DotProps = {
  delay: number;
};

function ThinkingDot({ delay }: DotProps) {
  const scale = useMemo(() => new Animated.Value(0.6), []);
  const opacity = useMemo(() => new Animated.Value(0.3), []);

  useEffect(() => {
    const anim = Animated.loop(
      Animated.sequence([
        Animated.delay(delay),
        Animated.parallel([
          Animated.timing(scale, {
            toValue: 1,
            duration: 400,
            easing: Easing.out(Easing.ease),
            useNativeDriver: true,
          }),
          Animated.timing(opacity, {
            toValue: 1,
            duration: 400,
            useNativeDriver: true,
          }),
        ]),
        Animated.parallel([
          Animated.timing(scale, {
            toValue: 0.6,
            duration: 400,
            easing: Easing.in(Easing.ease),
            useNativeDriver: true,
          }),
          Animated.timing(opacity, {
            toValue: 0.3,
            duration: 400,
            useNativeDriver: true,
          }),
        ]),
      ])
    );
    anim.start();
    return () => anim.stop();
  }, [delay, scale, opacity]);

  return <Animated.View style={[styles.dot, { transform: [{ scale }], opacity }]} />;
}

export function ThinkingBubble() {
  return (
    <View style={styles.row} accessibilityLiveRegion="polite" accessibilityLabel="绒星正在思考">
      <Image source={require('../../assets/figma/cat-avatar.png')} style={styles.avatar} />
      <View style={styles.bubble}>
        <View style={styles.dotsRow}>
          <ThinkingDot delay={0} />
          <ThinkingDot delay={200} />
          <ThinkingDot delay={400} />
        </View>
        <Text style={styles.text}>绒星正在思考…</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    marginBottom: 12,
    paddingHorizontal: 16,
  },
  avatar: {
    width: 32,
    height: 32,
    borderRadius: 16,
    marginRight: 8,
  },
  bubble: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.brandSoft,
    borderRadius: 18,
    borderBottomLeftRadius: 6,
    paddingHorizontal: 14,
    paddingVertical: 10,
    gap: 10,
  },
  dotsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.brand,
  },
  text: {
    fontSize: 12,
    color: colors.accent,
    fontWeight: '500',
  },
});
