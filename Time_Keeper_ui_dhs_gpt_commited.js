(() => {
  const $ = (q) => document.querySelector(q);

  async function fillWeatherNotice(){
    const box = $("#weatherDelayNotice");
    if(!box) return;

    try{
      const response = await fetch("/api/weather");
      if(!response.ok) throw new Error("weather api failed");

      const data = await response.json();

      if(data.weather_notice){
        box.textContent = `☔ ${data.weather_notice}`;
        box.hidden = false;
      }else{
        box.textContent = "";
        box.hidden = true;
      }
    }catch(error){
      box.textContent = "";
      box.hidden = true;
    }
  }

  function appendMetric(grid, label, value, marker){
    if(!grid || grid.querySelector(`[data-dhs-metric="${marker}"]`)){
      return;
    }

    const item = document.createElement("div");
    item.className = "metric";
    item.dataset.dhsMetric = marker;

    const span = document.createElement("span");
    span.textContent = label;

    const bold = document.createElement("b");
    bold.textContent = value;

    item.append(span, bold);
    grid.appendChild(item);
  }

  function enhanceRenderedRecommendation(data){
    const recommended = data?.recommended;
    if(!recommended) return;

    const grid = document.querySelector(
      "#resultContent .result-card .metric-grid"
    );

    if(!grid) return;

    if(recommended.travel_time_minutes != null){
      appendMetric(
        grid,
        "예상 소요시간",
        `${Number(recommended.travel_time_minutes).toFixed(1)}분`,
        "travel-time"
      );
    }

    if(recommended.estimated_arrival_time){
      appendMetric(
        grid,
        "예상 도착",
        String(recommended.estimated_arrival_time),
        "arrival-time"
      );
    }

    if(data?.model_scope?.highway_weather_applied){
      const cards = document.querySelectorAll(
        "#resultContent .result-card"
      );
      const reasonCard = cards.length > 1 ? cards[1] : null;

      if(reasonCard && !reasonCard.querySelector("[data-dhs-weather-applied]")){
        const note = document.createElement("div");
        note.className = "dhs-weather-applied";
        note.dataset.dhsWeatherApplied = "true";
        note.textContent = "기상 예보가 예상 소요시간에 반영되었습니다.";
        reasonCard.appendChild(note);
      }
    }
  }

  function hookRecommendationRenderer(){
    const original = window.renderRecommendation;

    if(typeof original !== "function") return;
    if(original.__dhsGptCommitedWrapped) return;

    function wrapped(data){
      original(data);
      enhanceRenderedRecommendation(data);
    }

    wrapped.__dhsGptCommitedWrapped = true;
    window.renderRecommendation = wrapped;
  }

  function initDhsUi(){
    hookRecommendationRenderer();
    fillWeatherNotice();
  }

  if(document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", initDhsUi, {once:true});
  }else{
    initDhsUi();
  }
})();
