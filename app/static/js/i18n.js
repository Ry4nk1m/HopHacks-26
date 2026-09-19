// Text strings for the app (English only right now) and small helpers to look them up and fill in values.

import { S, store } from './state.js';

// All display text, keyed by short id. {name} placeholders get filled in by t().
const EN = {
  app_name: 'Parasol', by_club: 'A {site} project', boot_map: 'Loading map…',
  onboard_title: 'Small tasks, real neighborhood impact',
  onboard_sub: 'Satellites show where help is needed. You do a quick task nearby, take a photo, and Gemini checks it.',
  nickname: 'Pick a nickname', nickname_hint: '2–20 characters. Other volunteers will see it.',
  safe_1: 'I will stay on public sidewalks and watch for traffic.',
  safe_2: "I won't photograph people, license plates or private property.",
  safe_3: 'This is not an emergency service. In an emergency I will call 911.',
  start: 'Start',
  err_nickname_taken: 'That nickname is taken. Try another.',
  err_bad_nickname: 'Use 2–20 letters or numbers (spaces . - _ allowed) with at least one letter.',
  err_network: "Couldn't reach the server. Check your connection.", err_generic: 'Something went wrong. Try again.',
  err_agree: 'Please confirm the three safety points.',
  f_all: 'All', f_tree: 'Trees', f_drain: 'Drains', f_cooling: 'Cooling', f_reports: 'Reports',
  type_tree_water: 'Tree needs water', type_drain_clear: 'Storm drain to clear', type_cooling_check: 'Verify cooling space',
  type_flood_report: 'Flooding', type_problem_report: 'Street problem',
  cat_storm_inlet_choke: 'Clogged storm inlet', cat_flooded_street: 'Flooded street', cat_damaged_inlet: 'Damaged storm inlet',
  cat_illegal_dumping: 'Illegal dumping', cat_litter: 'Dirty street or litter', cat_fallen_tree: 'Fallen tree', cat_broken_branch: 'Broken branch',
  cat_tree_issue: 'Tree needs city attention', cat_flooded_road: 'Flooded road', cat_blocked_drain: 'Blocked drain', cat_pothole: 'Pothole',
  cat_other: 'Other problem', cat_none: 'Nothing found',
  urg_1: 'Low priority', urg_2: 'Medium priority', urg_3: 'High priority',
  tag_unconfirmed: 'Unconfirmed report', tag_claimed: 'Someone is on it', tag_pending: 'Awaiting review', tag_mine: 'Your report',
  tag_city: 'Baltimore 311', tag_official: 'Official cooling center', tag_confirmed: 'Confirmed',
  r_dry_spell: 'Dry spell: {past} mm of rain this past week and only {next} mm expected. Young trees are at risk.',
  r_heat_forecast: 'Heat: it will feel like {f}°F in the next 3 days.',
  r_rain_forecast: 'Heavy rain expected: about {mm} mm in the next 2 days. Clear drains hold water back.',
  r_hours_unverified: "Opening hours haven't been verified recently.",
  r_city_request: 'Reported to Baltimore 311 {when} ({status}).', r_user_report: 'Reported by a volunteer.',
  sat_title: 'Satellite view of the area',
  sat_line: 'Vegetation index {ndvi}: {veg}. Ground surface about {f}°F: {heat}.',
  sat_veg_low: 'less green than most of the area', sat_veg_mid: 'about average for the area', sat_veg_high: 'greener than most of the area',
  sat_heat_high: 'hotter than most of the area', sat_heat_mid: 'typical for the area', sat_heat_low: 'cooler than most of the area',
  sat_src: 'Sentinel-2 vegetation ({ndvi}) and Landsat 9 surface heat ({lst}), about 30 m per pixel. It describes the surroundings, not this exact spot.',
  task_tree_water: 'Water the tree slowly at its base, about two buckets (5 gallons). Then photograph the tree with the wet soil at its base.',
  task_drain_clear: 'Clear leaves and litter from the top of the grate using gloves or a rake. Never lift the grate. Photograph the grate afterward.',
  task_cooling_check: 'Photograph the entrance or the posted opening hours. If you can, note whether the entrance has steps or is level.',
  task_confirm: 'Check whether the problem is still there and take a photo of the spot.',
  safety: 'Stay on the sidewalk, watch for traffic and keep your distance from anyone nearby.',
  what_to_do: 'What to do', why_flagged: 'Why it is flagged', details: 'Details',
  info_address: 'Address', info_hours: 'Hours', info_phone: 'Phone', info_verified: 'Last verified {when}: {hours}', info_verified_nohours: 'Last verified {when}.',
  info_accessible: 'Level entrance', info_steps: 'Steps at the entrance', note_label: 'Reporter note',
  accept: 'Accept mission', continue_m: 'Continue mission', claimed_btn: 'Someone else is on it', pending_btn: 'Awaiting review',
  confirm_btn: "Confirm it's here", directions: 'Directions', close: 'Close', done: 'Done', cancel_mission: 'Cancel mission',
  m_head_there: 'Head there', m_take_photos: 'Take photos', m_checking_step: 'Photo check',
  m_away: '{d} away', m_arrived: "You're here", m_imhere: "I'm here", m_far: 'Get closer: about {d} away, and you need to be within {r}.',
  m_before: 'Before photo (optional, +5 pts)', m_after: 'After photo', m_take: 'Tap to take a photo', m_retake: 'Tap to retake',
  m_real: 'Did this really need attention?', m_yes: 'Yes', m_no: 'No', m_unsure: 'Not sure',
  m_submit: 'Submit for photo check', m_checking: 'Gemini is checking your photo…', m_sim: 'Demo mode: this photo check is simulated.',
  m_tries: '{n} tries left', m_need_photo: 'Take the after photo first.', m_photo_hint_confirm: 'Photo of the spot',
  m_gps_waiting: 'Waiting for a location fix…',
  v_verified: 'Verified', v_pending: 'Sent for review', v_rejected: 'Not accepted', v_error: "Couldn't check",
  v_says: 'Photo check', v_retry: 'Try again',
  code_verified: 'Nice work! Your photo checks out.',
  code_pending_review: "We're not fully sure yet, so a person will review it. You'll get points if it's approved.",
  code_privacy: 'The photo shows a person or private information. Retake it without them.',
  code_bad_photo: 'The photo is too dark, blurry or far away. Retake it.', code_too_blurry: 'That photo is too blurry. Hold steady and retake it.',
  code_wrong_subject: "The photo doesn't seem to show the right thing. Move closer to the target.",
  code_task_not_done: "We can't see that the task is done yet.", code_low_confidence: "We couldn't verify that photo. Try a clearer shot.",
  code_duplicate_photo: 'That photo was already used. Take a new one.', code_unclear: "We can't tell if the problem is still there. Retake it from a clearer angle.",
  code_validator_unavailable: "The photo checker is unavailable. Your try wasn't used. Please retry in a moment.",
  code_no_arrival: 'Arrive at the spot first.', code_arrival_expired: 'You arrived a while ago. Tap "I\'m here" again.',
  code_too_far: 'You seem to be too far from the spot.', code_attempts_exhausted: 'No tries left for this mission.',
  code_not_active: 'This mission is no longer active.', code_claimed: 'Someone else is already on this one.',
  code_flag_unavailable: 'This one is no longer available.', code_too_many_missions: 'You already have 3 active missions.',
  code_recently_done: 'You already did this one recently.', code_own_report: "You can't verify your own report.",
  code_rate_limited: 'Slow down a little and try again soon.', code_mission_not_found: 'Mission not found.',
  code_loc_denied: 'Location is blocked. Allow it for this site in your browser settings, then try again.',
  code_loc_unavailable: "We can't find your location. Step outside, check location is on, and try again.",
  code_loc_timeout: 'Finding your location took too long. Try again with a clear view of the sky.',
  code_bad_location: "We couldn't read your location.", code_outside_area: "That's outside the area we cover.",
  code_weak_gps: 'Your GPS signal is too weak. Move to open sky and try again.', code_no_problem_seen: "We couldn't see a problem in that photo.",
  code_report_created: 'Report added. Thanks!', code_duplicate_report: 'This was already reported nearby.',
  code_already_confirmed: 'This already has a confirmation.', code_bad_type: 'Choose a report type.', code_too_large: 'That photo is too large.',
  code_unauthorized: 'Please sign in again.', code_generic: 'Something went wrong. Try again.', code_network: "Couldn't reach the server. Check your connection.",
  earned: '+{n} points', streak_days: '{n}-day streak', new_badge: 'New badge: {name}',
  rep_title: 'Report a problem', rep_help: 'Take a photo of the problem. We work out what it is from the picture.',
  rep_note: 'Add a note (optional)', rep_photo: 'Photo of the problem', rep_submit: 'Submit report', rep_loc: 'Location accuracy: {a}',
  rep_created: 'Report added', rep_unconfirmed: 'It shows as unconfirmed until a neighbor confirms it. You earn points then.',
  rep_points: 'You earned {n} points.', rep_view_existing: 'View the existing report', rep_locate_first: 'We need your location to file a report.',
  confirm_done: 'Thanks for confirming! +{n} points.',
  prof_title: 'Your profile', prof_points: 'Points', prof_streak: 'Day streak', prof_best: 'Best streak', prof_week: 'This week',
  prof_badges: 'Badges', prof_board: 'Leaderboard', prof_weekly: 'This week', prof_all: 'All time', prof_impact: 'Neighborhood impact',
  prof_impact_line: 'verified missions {n} · volunteers {v} · open flags {o}', prof_settings: 'Settings', prof_voice: 'Voice briefings',
  prof_demo: 'Demo tools', prof_fake: 'Pretend to be at a flag', prof_fake_help: 'Pick a flag to teleport next to it, or long-press the map to teleport anywhere.',
  prof_real: 'Use my real location', prof_force: 'Force conditions', force_heat: 'Heat', force_rain: 'Heavy rain', force_dry: 'Dry spell',
  force_auto: 'Auto', force_on: 'On', force_off: 'Off', prof_refresh: 'Refresh live data', prof_reset: 'Reset demo data',
  prof_reset_confirm: 'Reset all points, missions and reports?', prof_validator_mock: 'Photo checks are simulated (no Gemini key).',
  prof_validator_gemini: 'Photo checks use Gemini.', prof_none_yet: 'Nobody yet.', prof_you: 'you', teleported: 'Demo location set',
  b_first_mission: 'First mission', b_ten_missions: '10 missions', b_tree_friend: 'Tree friend', b_storm_ready: 'Storm ready',
  b_cool_scout: 'Cool scout', b_eyes_on_street: 'Eyes on the street', b_streak_3: '3-day streak', b_streak_7: '7-day streak',
  ov_title: 'Satellite layers', ov_off: 'Off', ov_ndvi: 'Vegetation (Sentinel-2, 30 m)', ov_lst: 'Surface heat (Landsat, 30 m)',
  ov_note_ndvi: 'Clear scenes: {dates}', ov_note_lst: 'Scenes: {dates}, about 10:30 am',
  ov_low_green: 'Less green', ov_high_green: 'More green', ov_cool: 'Cooler', ov_hot: 'Hotter',
  cond_heat: 'Heat alert', cond_rain: 'Heavy rain expected', cond_dry: 'Dry spell', cond_demo: 'demo',
  nearest: 'Nearest', nothing_near: 'No flags match this filter', mission_active: 'Mission in progress',
  gps_denied: 'Location is off. Turn it on to see flags near you.', gps_waiting: 'Finding your location…',
  gps_outside: "You're outside the Homewood area, so no flags are near you.", gps_weak: 'Weak GPS (±{a}). Move to open sky.',
  use_demo: 'Use demo location', demo_banner: 'Demo mode: photo checks are simulated.',
  just_now: 'just now', min_ago: '{n} min ago', hours_ago: '{n} h ago', days_ago: '{n} d ago',
  brief: 'Mission accepted. {title}. {task} Stay safe and watch for traffic.',
  pts: '{n} pts', flag_here: 'Flag', open_maps: 'Open in Maps', flags_count: '{n} flags',
  ob_tagline: 'Small tasks. Real neighborhood impact.',
  ob_welcome_sub: 'Help your neighborhood in five minutes, with a little help from satellites and AI.',
  ob_how1_t: 'Satellites spot the need', ob_how1_d: 'Heat, dry trees and city reports show where help matters most.',
  ob_how2_t: 'Walk to a nearby task', ob_how2_d: 'Water a tree, clear a drain, check a cooling center.',
  ob_how3_t: 'Snap a photo, earn points', ob_how3_d: 'Gemini checks your photo so every task counts.',
  ob_start: 'Get started', ob_continue: 'Continue', ob_back: 'Back', ob_done: "Let's go",
  ob_name_title: 'What should we call you?', ob_name_sub: 'Other volunteers will see this nickname.', ob_surprise: 'Surprise me',
  ob_voice_title: 'Voice guide', ob_voice_desc: 'Hear each mission read aloud, hands-free.', ob_voice_sample_btn: 'Hear a sample',
  ob_voice_sample: 'Mission accepted. Water this tree slowly at its base. Stay safe and watch for traffic.',
  ob_safe_title: 'Stay safe out there', ob_safe_sub: 'Tap each one to agree.', ob_agree: 'I understand',
  ob_loc_title: 'Find tasks near you', ob_loc_desc: 'Turn on location so we can show tasks around you and check that you have arrived.',
  ob_loc_privacy: 'Your location is never shown to other volunteers. We only keep it when you submit a photo.',
  ob_loc_enable: 'Turn on location', ob_loc_skip: 'Not now', ob_step: 'Step {n} of {t}',
  gps_off: 'Location is off. Turn it on to see flags near you.', voice_on: 'Voice guide on', voice_off: 'Voice guide off',
  menu_open: 'Open menu', menu_close: 'Close menu', menu_profile: 'My profile', menu_profile_sub: 'Points, badges and the leaderboard',
  menu_about: 'About and data credits', mission_h: 'Our mission',
  mission_body: 'Heat is one of the deadliest kinds of weather, and it hits hardest on blocks with little shade and few places to cool off. Parasol uses satellite heat and vegetation maps to find those blocks, then points neighbors to small jobs that help: water a struggling street tree, check that a cooling center is open, clear a drain before a storm. A photo, checked by AI, proves each job got done.',
  about_btn: 'About and data credits', about_title: 'About this app', about_privacy_h: 'Privacy and limits', about_data: 'Data sources',
  about_intro: 'Satellites and city data show where small tasks would help. Volunteers do them and prove it with a photo that Gemini checks.',
  about_privacy: 'We store your nickname, points and the photos you submit. Photos are re-encoded to remove location details and deleted after {days} days. Please keep people and private information out of photos.',
  about_limits: 'Satellite layers describe the surrounding area (about 30 m per pixel), not a specific tree or drain. Photo checks can be wrong, so borderline ones go to a person for review.',
  credit_map: 'Map data © OpenStreetMap contributors (ODbL). Tiles by OpenFreeMap, © OpenMapTiles.',
  credit_sat: 'Satellite imagery: Copernicus Sentinel-2 (ESA) and Landsat 9 (NASA/USGS), read from Microsoft Planetary Computer.',
  credit_weather: 'Weather: Open-Meteo. Alerts: U.S. National Weather Service.',
  credit_city: 'City data: Baltimore City Open Data (311 service requests, Code Red cooling centers).',
  credit_ai: 'Photo checks: Google Gemini. Voice: ElevenLabs.',
};

// Look up a text string by key, filling in any {placeholder} values. Falls back to the key itself if missing.
export function t(key, vars) {
  let s = EN[key] || key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  return s;
}

// Check if a text key exists.
export const has = (key) => Object.prototype.hasOwnProperty.call(EN, key);

// Get the label for a report category, falling back to a generic "other" label.
export function catLabel(code) {
  return has(`cat_${code}`) ? t(`cat_${code}`) : t('cat_other');
}

// Build the display title for a flag, based on its type and context.
export function flagTitle(flag) {
  const ctx = flag.context || {};
  if (flag.type === 'cooling_check') {
    const name = ctx.feature && ctx.feature.name;
    return name ? `${t('type_cooling_check')}: ${name}` : t('type_cooling_check');
  }
  if (ctx.city && ctx.city.category) return catLabel(ctx.city.category);
  if (ctx.report && ctx.report.category && ctx.report.category !== 'none') return catLabel(ctx.report.category);
  return t(`type_${flag.type}`);
}

// Get the user-facing message for an API result code, falling back to a generic error message.
export function codeMessage(code) {
  const k = `code_${code}`;
  return has(k) ? t(k) : t('code_generic');
}
