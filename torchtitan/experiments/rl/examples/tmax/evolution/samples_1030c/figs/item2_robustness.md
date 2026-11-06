
### original (rev must match)
| | -1 | +1 | +2 | +3 |
|---|---|---|---|---|
| easier, accepted | 0.01(n=1378) | 3.22(n=1210) | 4.99(n=673) | 5.89(n=484) |
| easier, rejected/failed | 0.01(n=157) | 0.53(n=124) | 0.89(n=66) | 1.16(n=44) |
| harder, accepted | 11.95(n=1028) | 6.86(n=889) | 7.59(n=473) | 7.62(n=298) |
| harder, rejected/failed | 12.00(n=139) | 11.66(n=117) | 11.58(n=55) | 11.81(n=26) |

### fixed sample: +1, +2, +3 all on the same rev
| | -1 | +1 | +2 | +3 |
|---|---|---|---|---|
| easier, accepted | 0.01(n=484) | 4.40(n=484) | 5.29(n=484) | 5.89(n=484) |
| easier, rejected/failed | 0.00(n=44) | 1.14(n=44) | 1.27(n=44) | 1.16(n=44) |
| harder, accepted | 11.97(n=298) | 6.39(n=298) | 7.03(n=298) | 7.62(n=298) |
| harder, rejected/failed | 12.00(n=26) | 11.54(n=26) | 11.58(n=26) | 11.81(n=26) |

### count after rev changes (first 3 trained groups after the rewrite, any rev)
| | -1 | +1 | +2 | +3 |
|---|---|---|---|---|
| easier, accepted | 0.01(n=1378) | 3.20(n=1278) | 4.16(n=1145) | 4.92(n=1008) |
| easier, rejected/failed | 0.01(n=157) | 0.77(n=137) | 1.62(n=116) | 2.39(n=97) |
| harder, accepted | 11.95(n=1028) | 6.81(n=902) | 7.27(n=753) | 7.22(n=609) |
| harder, rejected/failed | 12.00(n=139) | 11.66(n=118) | 9.60(n=100) | 8.85(n=82) |

### excluding security tasks
| | -1 | +1 | +2 | +3 |
|---|---|---|---|---|
| easier, accepted | 0.01(n=1277) | 3.22(n=1116) | 5.04(n=619) | 5.94(n=449) |
| easier, rejected/failed | 0.01(n=82) | 0.84(n=62) | 1.71(n=28) | 2.29(n=17) |
| harder, accepted | 11.95(n=980) | 6.91(n=844) | 7.68(n=454) | 7.65(n=285) |
| harder, rejected/failed | 12.00(n=125) | 11.65(n=106) | 11.59(n=49) | 11.91(n=23) |

### signal group: all 12 submitted
| | -1 | +1 | +2 | +3 |
|---|---|---|---|---|
| easier, accepted | 0.02(n=259) | 4.47(n=231) | 6.03(n=123) | 6.39(n=79) |
| easier, rejected/failed | 0.00(n=24) | 0.69(n=16) | 0.43(n=7) | 2.33(n=3) |
| harder, accepted | 11.97(n=1015) | 6.88(n=877) | 7.62(n=469) | 7.63(n=295) |
| harder, rejected/failed | 12.00(n=139) | 11.66(n=117) | 11.58(n=55) | 11.81(n=26) |

### signal group: some not submitted
| | -1 | +1 | +2 | +3 |
|---|---|---|---|---|
| easier, accepted | 0.01(n=1119) | 2.93(n=979) | 4.76(n=550) | 5.79(n=405) |
| easier, rejected/failed | 0.01(n=133) | 0.51(n=108) | 0.95(n=59) | 1.07(n=41) |
| harder, accepted | 10.54(n=13) | 5.25(n=12) | 3.50(n=4) | 6.00(n=3) |
| harder, rejected/failed | - | - | - | - |

### excluding security, signal group all 12 submitted
| | -1 | +1 | +2 | +3 |
|---|---|---|---|---|
| easier, accepted | 0.02(n=235) | 4.65(n=208) | 6.12(n=111) | 6.40(n=75) |
| easier, rejected/failed | 0.00(n=14) | 1.11(n=9) | 0.75(n=4) | 7.00(n=1) |
| harder, accepted | 11.97(n=968) | 6.92(n=833) | 7.72(n=450) | 7.66(n=282) |
| harder, rejected/failed | 12.00(n=125) | 11.65(n=106) | 11.59(n=49) | 11.91(n=23) |