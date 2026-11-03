#!/bin/bash
# usage: sheet.sh clip.webm out.png "w:h:x:y" outw cols t1 t2 ...   -> grid sheet with time labels (crop is required)
clip=$1; out=$2; crop=$3; ow=$4; cols=$5; shift 5
cw=${crop%%:*}; rest=${crop#*:}; ch=${rest%%:*}
oh=$(( ch * ow / cw ))
i=0; inputs=""; filt=""
for t in "$@"; do
  inputs="$inputs -ss $t -i $clip"
  filt="$filt[$i:v]trim=end_frame=1,crop=$crop,scale=$ow:$oh,drawtext=fontfile='C\:/Windows/Fonts/arialbd.ttf':text='$t':x=6:y=6:fontsize=28:fontcolor=red:box=1:boxcolor=white[v$i];"
  i=$((i+1))
done
n=$i; lay=""; ins=""
for ((k=0;k<n;k++)); do r=$((k/cols)); c=$((k%cols)); lay="$lay${lay:+|}$((c*ow))_$((r*oh))"; ins="$ins[v$k]"; done
filt="$filt${ins}xstack=inputs=$n:layout=$lay[o]"
ffmpeg -v error -y $inputs -filter_complex "$filt" -map "[o]" -frames:v 1 $out
