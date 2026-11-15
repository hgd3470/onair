"""운영 서버 안에서 도는 추천 틀 (S2-09).

규칙 기반 추천(playlist/ranker.py)을 기준선으로 두고, 그 점수 위에
전략별 가산점을 얹어 순서를 다시 정한다. 전략은 rule(가산점 0), bandit(Thompson
sampling 탐색), ease(청취 기록 협업 필터링) 세 가지다. 콘텐츠 제작 파이프라인과는
따로 돈다. 자세한 방법론은 README.md.
"""
