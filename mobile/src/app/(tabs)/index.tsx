import { Link, useFocusEffect, useIsFocused, useRouter } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { AvatarStage, type Submission } from '../../components/AvatarStage';
import { MotionStage } from '../../components/MotionStage';
import { GifExport } from '../../components/GifExport';
import { PhotoUpload } from '../../components/PhotoUpload';
import { Screen } from '../../components/Screen';
import { Button } from '../../components/ui/Button';
import { Icon } from '../../components/ui/Icon';
import { SectionHeader } from '../../components/ui/SectionHeader';
import { api } from '../../lib/api';
import { emotionBus } from '../../lib/chat-emotion';
import { tzOffsetMinutes } from '../../lib/diet';
import { formatDateTime, isUrgent, stateLabel, totalEffective, type HealthSummary } from '../../lib/health';
import { canUse, type QuotaSnapshot } from '../../lib/quota';
import { ApiError } from '../../lib/session';
import { colors, radius, spacing, typography } from '../../theme';

// 健康状态→语义色（v2 Foundations：success/warning/danger）
const STATE_STYLE: Record<string, { bg: string; fg: string }> = {
  normal: { bg: colors.successBg, fg: colors.success },
  observe: { bg: colors.warningBg, fg: colors.warning },
  vet: { bg: colors.dangerBg, fg: colors.danger },
  urgent: { bg: colors.dangerBg, fg: colors.danger },
};
const PROFILE_STATES = {
  loading: { title: '正在加载猫咪档案', detail: '正在连接绒星服务…', action: '' },
  signedOut: { title: '欢迎来到绒星', detail: '登录后，就能查看猫咪档案和健康记录。', action: '去登录' },
  consent: { title: '请先阅读并同意协议', detail: '同意后，即可查看和管理猫咪档案。', action: '查看协议' },
  ready: { title: '添加第一只猫咪', detail: '为它创建一份档案，从今天开始记录饮食、体重和健康。', action: '前往「我的」创建档案' },
  error: { title: '暂时无法加载猫咪档案', detail: '请检查网络连接，或稍后再试。', action: '重新加载' },
  configuration: { title: '还未连接绒星服务', detail: __DEV__
    ? '请先启动本地 API，再将 Expo 指向该服务并重启。'
    : '当前版本无法连接服务，请更新到最新版本后再试。', action: '' },
};
const STATE_HINTS: Record<string, string> = {
  normal: '状态良好，继续保持。',
  observe: '建议观察精神与食欲变化，按时复查。',
  vet: '建议尽快安排就医检查。',
};
// 未登录首页的萌宠位：待机动图（底色已与 heroBg 对齐）优先，GIF 解码失败退回静态插画。
const WELCOME_CAT_MOTION = require('../../../assets/motion/idle.gif');
const WELCOME_CAT_STILL = require('../../../assets/figma/59f10c90466cfa3aa84c0f88478dccedc8f69565.png');

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 11) return '早上好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

export default function HomeScreen() {
  const router = useRouter();
  const isFocused = useIsFocused();
  const [pets, setPets] = useState<{ id: number; name: string }[]>([]);
  const [selectedId, setSelectedId] = useState<number>();
  const [petsState, setPetsState] = useState<keyof typeof PROFILE_STATES>('loading');
  const [retry, setRetry] = useState(0);
  const [submission, setSubmission] = useState<(Submission & { petId: number }) | undefined>();
  const [healthResult, setHealthResult] = useState<{ petId: number; data: HealthSummary | null }>();
  const [showDeductions, setShowDeductions] = useState(false);
  const [emotionEvent, setEmotionEvent] = useState<{ id: string; petId: number; emotion: string } | null>(null);
  const [quota, setQuota] = useState<QuotaSnapshot>();
  const [catMotionFailed, setCatMotionFailed] = useState(false);
  // F09-5：聊聊页回复情绪→仅当前选中猫的分身播一次对应动作后回待机。
  useEffect(() => emotionBus.subscribe(event => setEmotionEvent({ id: String(event.id), petId: event.petId, emotion: event.emotion })), []);
  const selected = pets.find(pet => pet.id === selectedId) ?? pets[0];
  useEffect(() => {
    if (!isFocused) return;
    let active = true;
    api.authorized<{ id: number; name: string }[]>('/pets').then(items => {
      if (!Array.isArray(items)) throw new Error('Invalid pet list');
      if (active) { setPets(items); setPetsState('ready'); }
    }).catch(error => {
      if (active) {
        setPets([]);
        setPetsState(error instanceof ApiError && error.status === 401 ? 'signedOut'
          : error instanceof ApiError && error.code === 'CONSENT_REQUIRED' ? 'consent'
          : error instanceof ApiError && error.code === 'API_NOT_CONFIGURED' ? 'configuration' : 'error');
      }
    });
    api.authorized<QuotaSnapshot>('/quota').then(snapshot => { if (active) setQuota(snapshot); }).catch(() => {});
    return () => { active = false; setPets([]); setPetsState('loading'); setHealthResult(undefined); };
  }, [isFocused, retry]);
  // 健康聚合随焦点与切猫重拉（AC-02-4 / AC-04-1 刷新一致性）。
  const selectedPetId = selected?.id;
  useFocusEffect(useCallback(() => {
    setShowDeductions(false);
    if (!selectedPetId) return;
    let active = true;
    api.authorized<HealthSummary>('/pets/' + selectedPetId + '/health?tz_offset_minutes=' + tzOffsetMinutes())
      .then(data => { if (active) setHealthResult({ petId: selectedPetId, data }); })
      .catch(() => { if (active) setHealthResult({ petId: selectedPetId, data: null }); });
    return () => { active = false; };
  }, [selectedPetId]));
  const currentHealth = healthResult?.petId === selectedPetId ? healthResult : undefined;
  const health = currentHealth?.data ?? null;
  const healthFallback = currentHealth ? '暂不可用' : '加载中';
  const urgent = health !== null && isUrgent(health.state);
  const assessed = health !== null && health.score !== null;
  const stateStyle = assessed ? STATE_STYLE[health.state] ?? { bg: colors.neutralPillBg, fg: colors.disabledText } : { bg: colors.neutralPillBg, fg: colors.disabledText };
  const hint = assessed
    ? (urgent && health.urgent_card ? health.urgent_card.subtitle : STATE_HINTS[health.state] ?? '')
    : health ? `还没有为${selected?.name}做过初筛，花 1 分钟了解它的状态`
      : currentHealth ? '暂时无法取得最新健康状态。' : '正在读取最新健康状态…';
  const cyclePet = () => {
    if (pets.length < 2 || !selected) return;
    const index = pets.findIndex(pet => pet.id === selected.id);
    setSelectedId(pets[(index + 1) % pets.length].id);
  };
  const profileState = PROFILE_STATES[petsState];
  return (
    <Screen contentStyle={styles.screenContent}>
      {/* 问候＋切换猫咪（画板 22:309 头部） */}
      <View style={styles.header}>
        <View style={styles.headerText}>
          <Text accessibilityRole="header" style={styles.greeting}>{greeting()}</Text>
          <Text style={styles.subtitle}>{selected ? `${selected.name}今天也需要你的留意` : '星光护绒，健康相伴'}</Text>
        </View>
        {selected ? (
          <View style={styles.headerPet}>
            <Image source={require('../../../assets/figma/cat-avatar.png')} style={styles.headerAvatar} accessibilityLabel={`${selected.name}的头像`} />
            <Pressable accessibilityRole="button" accessibilityLabel="切换猫咪" accessibilityValue={{ text: selected.name }} hitSlop={{ top: 7, bottom: 7 }} onPress={cyclePet} style={styles.switchPill}>
              <Text style={styles.switchText} numberOfLines={1}>{selected.name} ⌄</Text>
            </Pressable>
          </View>
        ) : null}
      </View>
      {pets.length > 1 ? (
        <View style={styles.selector}>
          {pets.map(pet => (
            <Pressable key={pet.id} accessibilityRole="button" accessibilityState={{ selected: selected?.id === pet.id }}
              onPress={() => setSelectedId(pet.id)} style={[styles.pet, selected?.id === pet.id && styles.activePet]}>
              <Text style={styles.petText}>{pet.name}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}
      {selected ? (
        <>
          {/* 品牌插画位：左文案＋右分身舞台（无分身时静态底图兜底，即画板视觉） */}
          <View style={styles.hero}>
            <Text style={styles.sparkle}>✦</Text>
            <View style={styles.heroText}>
              <Text style={styles.heroTitle}>{'用爱守护\n它的每一天'}</Text>
              <Text style={styles.heroSubtitle}>{'健康的陪伴\n从点滴开始'}</Text>
              <Text style={styles.heroTag}>·  陪伴每一天</Text>
            </View>
            <MotionStage key={selected.id} pet={selected}
              submission={submission?.petId === selected.id ? submission : undefined}
              health={health ? { state: health.state } : null}
              emotionEvent={emotionEvent?.petId === selected.id ? { id: emotionEvent.id, emotion: emotionEvent.emotion } : null}
              fallback={<AvatarStage pet={selected}
                submission={submission?.petId === selected.id ? submission : undefined}
                health={health ? { state: health.state } : null}
                emotionEvent={emotionEvent?.petId === selected.id ? { id: emotionEvent.id, emotion: emotionEvent.emotion } : null} />} />
          </View>
          {/* 健康状态卡片 */}
          <View style={[styles.healthCard, urgent && styles.healthCardUrgent]}>
            <Text style={styles.healthLabel}>健康状态</Text>
            <View style={styles.healthRow}>
              <Text style={[styles.score, !assessed && styles.scorePlaceholder, urgent && { color: colors.danger }]}>{assessed ? String(health.score) : '——'}</Text>
              <View style={[styles.statePill, { backgroundColor: stateStyle.bg }]}>
                <Text style={[styles.statePillText, { color: stateStyle.fg }]}>{assessed ? stateLabel(health.state) : health ? '未评估' : healthFallback}</Text>
              </View>
            </View>
            <Text style={styles.healthHint}>{hint}</Text>
            {health && health.deductions.length > 0 ? (
              <View style={styles.deductions}>
                <Pressable accessibilityRole="button" onPress={() => setShowDeductions(value => !value)}>
                  <Text style={styles.deductionToggle}>{showDeductions ? '收起扣分明细 ▴' : `扣分明细（${health.deductions.length} 项）▾`}</Text>
                </Pressable>
                {showDeductions ? health.deductions.map(item => (
                  <View key={item.id} style={styles.deductionItem}>
                    <Text style={styles.deductionLine}>
                      {(item.symptoms.length > 0 ? item.symptoms.join('、') : stateLabel(item.severity)) + '：−' + item.score_delta + ' 分'}
                      {item.decayed ? ' · 已衰减，计 0' : ''}
                    </Text>
                    <Text style={styles.deductionMeta}>{formatDateTime(item.created_at) + ' 生效 · 规则 ' + (item.rules_version ?? '—')}</Text>
                  </View>
                )) : null}
                {showDeductions ? <Text style={styles.deductionTotal}>当前有效扣分合计：{totalEffective(health.deductions)} 分</Text> : null}
              </View>
            ) : null}
            {urgent && health?.urgent_card ? (
              <Link href={`/screening/${selected.id}`} asChild>
                <Pressable accessibilityRole="button" style={styles.urgentLink}>
                  <Text style={styles.urgentLinkText}>{health.urgent_card.title} ›</Text>
                </Pressable>
              </Link>
            ) : null}
            {!urgent ? (
              <Button title={health?.latest_screening ? '再做一次初筛' : '开始健康初筛'}
                onPress={() => router.push(`/screening/${selected.id}`)} style={styles.cta} />
            ) : null}
          </View>
          <View style={styles.section}>
            <SectionHeader title="今日概览" actionLabel="查看全部  ›" onAction={() => router.push(`/timeline/${selected.id}`)} style={styles.sectionHeader} />
            <View style={styles.tiles}>
              <Link href={`/diet/${selected.id}`} asChild>
                <Pressable accessibilityRole="button" style={styles.tile}>
                  <Text style={styles.tileLabel}>今日饮食</Text>
                  <Icon name="food" size={22} color={colors.mutedIcon} />
                  <Text style={styles.tileValue}>{health?.diet_today && health.diet_today.count > 0 ? `${health.diet_today.total_g}g · ${health.diet_today.total_kcal}kcal` : health ? '暂无记录' : healthFallback}</Text>
                </Pressable>
              </Link>
              <Link href={`/weight/${selected.id}`} asChild>
                <Pressable accessibilityRole="button" style={styles.tile}>
                  <Text style={styles.tileLabel}>最新体重</Text>
                  <Icon name="scale" size={22} color={colors.mutedIcon} />
                  <Text style={styles.tileValue}>{health?.latest_weight ? `${health.latest_weight.weight_kg} kg` : health ? '暂无记录' : healthFallback}</Text>
                </Pressable>
              </Link>
              <Link href={`/screening/history/${selected.id}`} asChild>
                <Pressable accessibilityRole="button" style={styles.tile}>
                  <Text style={styles.tileLabel}>最近初筛</Text>
                  <Icon name="file" size={22} color={colors.mutedIcon} />
                  <Text style={styles.tileValue}>{health?.latest_screening ? stateLabel(health.latest_screening.state) : health ? '暂无记录' : healthFallback}</Text>
                </Pressable>
              </Link>
            </View>
          </View>
          <View style={styles.section}>
            <SectionHeader title="快捷操作" style={styles.sectionHeader} />
            <View style={styles.tiles}>
              <Link href={`/diet/${selected.id}`} asChild>
                <Pressable accessibilityRole="button" style={styles.quick}>
                  <Icon name="food" size={17} color={colors.text} />
                  <Text style={styles.quickLabel}>记录饮食</Text>
                </Pressable>
              </Link>
              <Link href={`/weight/${selected.id}`} asChild>
                <Pressable accessibilityRole="button" style={styles.quick}>
                  <Icon name="scale" size={17} color={colors.text} />
                  <Text style={styles.quickLabel}>记录体重</Text>
                </Pressable>
              </Link>
              <Link href="/(tabs)/chat" asChild>
                <Pressable accessibilityRole="button" style={StyleSheet.flatten([styles.quick, styles.quickChat])}>
                  <Icon name="chat" size={17} color={colors.accent} />
                  <Text style={[styles.quickLabel, { color: colors.accent }]}>和我聊聊</Text>
                </Pressable>
              </Link>
            </View>
          </View>
          {/* 萌宠动图上传与 GIF 分享（设计稿未覆盖的功能区，沿用既有组件） */}
          <SectionHeader title="萌宠动图与分享" />
          <PhotoUpload key={selected.id} pet={selected} endpoint="motion" quotaExhausted={!canUse(quota, 'avatar_gen')}
            onSubmitted={next => setSubmission({ petId: selected.id, ...next })} />
          <GifExport key={`gif-${selected.id}`} pet={selected} />
          <Link href={`/timeline/${selected.id}`} style={styles.profileLink}>健康时间线（初筛 · 饮食 · 体重）</Link>
        </>
      ) : (
        <>
          <View style={styles.welcomeHero}>
            <View style={styles.welcomeCopy}>
              <Text style={styles.welcomeTitle}>{'用爱守护\n它的每一天'}</Text>
              <Text style={styles.welcomeSubtitle}>从一份猫咪档案开始</Text>
            </View>
            <Image source={catMotionFailed ? WELCOME_CAT_STILL : WELCOME_CAT_MOTION}
              onError={() => setCatMotionFailed(true)}
              accessible={false} style={styles.welcomeCat} resizeMode="contain" />
          </View>
          <View style={styles.profileState}>
            <View accessibilityLiveRegion="polite">
              <Text accessibilityRole="header" style={styles.stateTitle}>{profileState.title}</Text>
              <Text style={styles.stateDetail}>{profileState.detail}</Text>
            </View>
            {profileState.action ? (
              <Button title={profileState.action} style={styles.cta} onPress={() => {
                if (petsState === 'error') {
                  setPetsState('loading');
                  setRetry(attempt => attempt + 1);
                } else {
                  router.navigate('/(tabs)/profile');
                }
              }} />
            ) : null}
          </View>
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  screenContent: { paddingTop: 0 },
  header: { marginLeft: 4, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  headerText: { flex: 1 },
  greeting: { fontSize: 25, lineHeight: 35, fontWeight: '600', color: colors.onBrand },
  subtitle: { fontSize: 13, lineHeight: 18, color: colors.secondary },
  headerPet: { flexDirection: 'row', alignItems: 'center', minHeight: 44, gap: 4 },
  headerAvatar: { width: 40, height: 40, borderRadius: 20 },
  switchPill: { minHeight: 31, borderRadius: 16, backgroundColor: colors.brandSoft, paddingHorizontal: 7, alignItems: 'center', justifyContent: 'center', maxWidth: 96 },
  switchText: { fontSize: 12, lineHeight: 17, fontWeight: '500', color: colors.text },
  selector: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  pet: { minHeight: 36, justifyContent: 'center', paddingHorizontal: 12, borderRadius: 12, backgroundColor: colors.surfaceRaised },
  activePet: { backgroundColor: colors.brandSoft },
  petText: { color: colors.text, fontWeight: '600', fontSize: 13 },
  hero: { flexDirection: 'row', backgroundColor: colors.heroBg, borderRadius: 22, paddingLeft: 16, paddingRight: 45, minHeight: 195, overflow: 'hidden' },
  sparkle: { position: 'absolute', top: 39, right: 18, fontSize: 17, lineHeight: 24, color: colors.sparkle, zIndex: 1 },
  heroText: { flex: 1, paddingTop: 37, paddingBottom: 10 },
  heroTitle: { fontSize: 20, lineHeight: 28, fontWeight: '600', color: colors.text },
  heroSubtitle: { marginTop: 16, fontSize: 13, lineHeight: 18, color: colors.secondary },
  heroTag: { fontSize: 11, lineHeight: 15, fontWeight: '500', color: colors.accent, marginTop: 25 },
  healthCard: { backgroundColor: colors.surfaceRaised, borderRadius: 20, paddingHorizontal: 16, paddingTop: 15, paddingBottom: 14 },
  healthCardUrgent: { backgroundColor: colors.dangerBg },
  healthLabel: { fontSize: 15, lineHeight: 21, fontWeight: '600', color: colors.text },
  healthRow: { flexDirection: 'row', alignItems: 'center', marginTop: 1, gap: 18 },
  score: { fontSize: 25, lineHeight: 35, fontWeight: '600', color: colors.text },
  scorePlaceholder: { minWidth: 50 },
  statePill: { minHeight: 26, borderRadius: 13, paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center' },
  statePillText: { fontSize: 12, fontWeight: '500' },
  healthHint: { fontSize: 12, lineHeight: 17, color: colors.secondary },
  deductions: { gap: 4, marginTop: 8 },
  deductionToggle: { color: colors.accent, fontWeight: '600', fontSize: 12 },
  deductionItem: { gap: 1 },
  deductionLine: { color: colors.text, fontSize: 11, lineHeight: 16 },
  deductionMeta: { color: colors.secondary, fontSize: 10, lineHeight: 14 },
  deductionTotal: { color: colors.text, fontSize: 11, fontWeight: '600' },
  cta: { height: 'auto', minHeight: 48, marginTop: 12, paddingVertical: 8 },
  urgentLink: { minHeight: 48, borderRadius: 24, backgroundColor: colors.danger, alignItems: 'center', justifyContent: 'center', marginTop: 12, padding: 8 },
  urgentLinkText: { fontSize: 16, textAlign: 'center', fontWeight: '600', color: '#FFFFFF' },
  section: { gap: 6 },
  sectionHeader: { paddingLeft: 4, minHeight: 24 },
  tiles: { flexDirection: 'row', gap: 12 },
  tile: { flex: 1, minHeight: 94, borderRadius: 16, backgroundColor: colors.surfaceRaised, alignItems: 'center', paddingHorizontal: 6, paddingTop: 13, paddingBottom: 8 },
  tileLabel: { fontSize: 12, lineHeight: 17, textAlign: 'center', fontWeight: '500', color: colors.text },
  tileValue: { fontSize: 12, lineHeight: 17, marginTop: 5, color: colors.secondary, textAlign: 'center' },
  quick: { flex: 1, minHeight: 52, borderRadius: 16, backgroundColor: colors.surfaceRaised, flexDirection: 'row', alignItems: 'center', gap: 4, padding: 7 },
  quickChat: { backgroundColor: colors.brandSoft },
  quickLabel: { flexShrink: 1, fontSize: 13, lineHeight: 18, fontWeight: '500', color: colors.text },
  welcomeHero: { flexDirection: 'row', alignItems: 'center', backgroundColor: colors.heroBg, borderRadius: radius.lg, paddingLeft: spacing.s5, overflow: 'hidden' },
  welcomeCopy: { flex: 1, paddingVertical: spacing.s5 },
  welcomeTitle: { ...typography.pageTitle, color: colors.onBrand },
  welcomeSubtitle: { ...typography.caption, color: colors.secondary, marginTop: spacing.s2 },
  welcomeCat: { width: '44%', maxWidth: 168, aspectRatio: 1, alignSelf: 'flex-end' },
  profileState: { paddingHorizontal: spacing.s5, paddingVertical: spacing.s4, backgroundColor: colors.surfaceRaised, borderRadius: radius.lg },
  stateTitle: { ...typography.sectionTitle, color: colors.text },
  stateDetail: { ...typography.caption, color: colors.secondary, marginTop: spacing.s1 },
  profileLink: { minHeight: 44, color: colors.accent, textDecorationLine: 'underline', paddingVertical: 12, fontSize: 13 },
});
