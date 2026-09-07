-- 003 — 옛 클립의 실제 길이를 채운다. 2026-09-07.
--
-- `clips.total_sec` 은 v9(001)에서 생겼지만 기존 경로(기준 rank → cut)가 채우지 않아 null 로 남았다.
-- 시청자 화면이 이 값으로 "N초" 를 보여주는데 null 이면 길이가 통째로 안 나온다.
--
-- 🔴 조각이 있는 클립은 건드리지 않는다. 조합 클립에서 `end_sec - start_sec` 은 **봉투**라
-- (첫 조각 시작 ~ 마지막 조각 끝) 12:30 과 41:00 을 이은 30초짜리가 28분이 된다.
-- 조각이 없는 클립 = 연속된 한 덩어리이므로 그때만 봉투가 곧 길이다.

update clips c
   set total_sec = c.end_sec - c.start_sec
 where c.total_sec is null
   and not exists (select 1 from clip_parts p where p.clip_id = c.id);
