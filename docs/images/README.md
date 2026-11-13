# Screenshot provenance

These PNGs were captured on 2026-11-14 from the current program at commit `36265c2`, using `ScienceFileReader`, `ViewerSession`, and `build_viewer`. Matplotlib's Agg canvas saved the entire application figure at 1600 × 1100 pixels (100 dpi); operating-system window decorations and the native toolbar are outside this canvas. The widgets, thermal images, statistics, and plots are produced by the program. No AI-generated interface, synthetic temperature data, raster retouching, or compositing was used.

The read-only source is the local `raw/square1.seq`: 7,625,825,755 bytes, 12,354 frames, 640 × 480 pixels. The three upright samples were visually checked before choosing fresh demonstration ROIs. Existing selections were not loaded against this source.

| Asset | Recorded state |
| --- | --- |
| `fig1-frame-range.png` | Process page; full source remains the working range; candidate frames 6192–9249 are entered but not yet confirmed; current source frame 6192; manual Fixed display 22–45 °C. |
| `fig2-rotate-crop-editor.png` | Candidate range has been confirmed (3058 frames); transform editor at local frame 0 / source frame 6192; draft angle −2.1° and crop `(210, 52, 414, 255)` in rotated-canvas pixels; preview limits remain 22–45 °C. |
| `fig3-rotate-crop-applied.png` | Applied transform; local frame 1500 / source frame 7692; exact scoped Auto range completed for all confirmed frames and the applied crop: 22.564563751220703–37.35793685913086 °C, `auto_ready`. |
| `process-overview.png` | Same genuine applied Process canvas as figure 3, retained as a top-of-README overview. |
| `fig4-add-roi.png` | Measure page, current-frame Distribution; three checked ROIs, with Sample 1 selected. Statistics refer to the actual active ROI at local frame 1500. |
| `fig5-multiple-roi-curves.png` | Completed Frame curves for all three checked ROIs over the shorter confirmed source interval 6192–7191 (1000 frames); current local frame 750 / source frame 6942; mean temperature and within-ROI temperature range. |
| `fig6-height-profile.png` | Height analysis Profile; confirmed source interval 6192–7191 (1000 frames), current local frame 750 / source frame 6942; all three checked ROIs; pixel height, Bottom hot direction, no physical-length calibration. |
| `fig7-height-zones.png` | Height analysis Zones; same 1000-frame interval as figures 5–6; three equal-height bands enabled for each ROI; curves derived from the completed per-row result store. |

ROI geometry uses cropped-image pixel coordinates, with names indicating left-to-right sample order only:

| Name | x | y | Width | Height | Colour |
| --- | ---: | ---: | ---: | ---: | --- |
| Sample 1 | 65 | 125 | 58 | 43 | `#1f77b4` |
| Sample 2 | 177 | 112 | 58 | 56 | `#ff7f0e` |
| Sample 3 | 291 | 115 | 61 | 53 | `#2ca02c` |

These rectangles were chosen inside the visible sample faces; no material identity or comparative material-performance claim is implied. Bottom hot describes the heater-below-samples orientation in this recording. Heights are relative pixel distances inside each ROI, not a shared physical height. The SDK's recorded object parameters determine temperature conversion; the display limits affect colour mapping only.

The capture script and temporary analysis data are confined to ignored `.packaging/`. The session caches retain at most three frames each. Native Auto and Height jobs are awaited and disposed before closing the SDK reader. Source recordings, clips, and existing selection files are checked for unchanged size and modification time. The existing video-preview asset is unchanged.

Figures 5–7 intentionally use a shorter confirmed interval for this documentation example. The earlier 3058-frame Height analysis reached the 180-second capture deadline, was cancelled and joined, and supplied no result to these plots. The replacement job calculates every one of the 1000 confirmed frames, producing 3000 standard ROI-statistic rows and 152000 canonical height rows; the program may reduce the rendered curve preview while retaining complete calculated data in its temporary store.

For figures 5–7, the exact scoped Auto limits are 22.564563751220703–35.66742706298828 °C (`auto_ready`). The complete 1000-frame interval spans 22.846 s; frame 750 is at 17.270 s relative to its first timestamp. SDK object parameters: emissivity 0.949999988079071; reflected and atmosphere temperature 293.1499938964844 K; distance 1.0 m; relative humidity 0.5; atmospheric transmission 0.9939431548118591. All three checked ROIs retain equal-third zones and Low−High as the zone-temperature difference.
