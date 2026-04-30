import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule, Router, ActivatedRoute } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { AnalysisService } from '../../core/services/analysis.service';

@Component({
  selector: 'app-job-analysis',
  standalone: true,
  imports: [CommonModule, RouterModule, FormsModule],
  templateUrl: './job-analysis.component.html',
  styleUrls: ['./job-analysis.component.css']
})
export class JobAnalysisComponent implements OnInit, OnDestroy {
  jobId: number = Math.floor(Math.random() * 9000) + 1000;
  sourceSchema: string = 'public';

  stages = [
    { num: 1, icon: '📂', name: 'Schema crawl', lib: 'SchemaCrawler + SQLAlchemy', status: 'waiting', pct: 0 },
    { num: 2, icon: '🔗', name: 'Column matching', lib: 'Valentine + RapidFuzz', status: 'waiting', pct: 0 },
    { num: 3, icon: '📊', name: 'Cardinality detection', lib: 'SQLAlchemy · COUNT', status: 'waiting', pct: 0 },
    { num: 4, icon: '🔐', name: 'PII & sensitive detect', lib: 'Presidio + spaCy NER', status: 'waiting', pct: 0 },
    { num: 5, icon: '🧠', name: 'Domain clustering', lib: 'sentence-transformers · DBSCAN', status: 'waiting', pct: 0 },
    { num: 6, icon: '🕸', name: 'Graph build', lib: 'NetworkX · ERD context', status: 'waiting', pct: 0 },
    { num: 7, icon: '🏛', name: 'Entity Classification', lib: 'Structural Heuristics Pipeline', status: 'waiting', pct: 0 }
  ];

  logs: { time: string, stage: string, msg: string, cls: string }[] = [];

  elapsed = '00:00';
  startTime = 0;
  overallPct = 0;
  jobStatus = 'starting…';
  showSummary = false;
  isJobRunning = false;
  jobCompleted = false;
  isSocketConnected = false;
  
  // Job Card State Control
  showOrchestrator = false;
  
  jobsList: any[] = [];
  connections: any[] = [];
  filterStatus: string = 'ALL';
  selectedSchema: string = '';
  
  private elapsedInt: any;

  constructor(private router: Router, private route: ActivatedRoute, private analysisService: AnalysisService) {}

  ngOnInit() {
    this.route.queryParams.subscribe(params => {
      if (params['src']) {
        this.sourceSchema = params['src'];
        this.jobStatus = `Target Schema: ${this.sourceSchema}`;
      } else {
        this.sourceSchema = '';
      }
    });

    this.loadJobs();
    this.loadConnections();
  }

  loadConnections() {
    this.analysisService.getConnections().subscribe({
      next: (data) => {
        this.connections = data;
        if (data.length > 0) this.selectedSchema = data[0].schemaName;
      },
      error: (err) => console.error("Error loading connections list natively", err)
    });
  }

  loadJobs() {
    this.analysisService.getJobs().subscribe({
      next: (data) => {
        // Sort descending locally to show newest jobs first
        this.jobsList = data.sort((a, b) => b.id - a.id);
      },
      error: (err) => console.error("Error loading jobs history", err)
    });
  }

  filteredJobs() {
    if (this.filterStatus === 'ALL') return this.jobsList;
    return this.jobsList.filter(j => j.status === this.filterStatus);
  }

  ngOnDestroy() {
    if (this.elapsedInt) clearInterval(this.elapsedInt);
  }

  triggerJob() {
    const target = this.sourceSchema ? this.sourceSchema : this.selectedSchema;
    if (!target) return;
    this.sourceSchema = target;

    this.showOrchestrator = true;
    this.isJobRunning = true;
    this.jobStatus = `Provisioning backend environment...`;

    this.analysisService.triggerAnalysis(this.sourceSchema).subscribe({
      next: (jobEntity) => {
        this.jobId = jobEntity.id;
        
        this.jobStatus = `Target Schema: ${this.sourceSchema} · running...`;
        this.startTime = Date.now();
        this.elapsedInt = setInterval(() => {
          const s = Math.floor((Date.now() - this.startTime) / 1000);
          this.elapsed = String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
        }, 500);

        // Bind Live SSE Source
        const eventSource = this.analysisService.streamAnalysis(this.jobId);
        
        eventSource.onopen = () => {
          this.isSocketConnected = true;
        };
        
        eventSource.onmessage = (event) => {
          const data = JSON.parse(event.data);
          this.logs.push({ time: this.ts(), stage: data.stage, msg: data.msg, cls: data.cls });
          
          this.syncStagesFromLog(data.stage, data.pct);
          
          if (data.stage === '[DONE]' || data.stage === '[ERROR]') {
            this.isSocketConnected = false;
            eventSource.close();
            this.finalizeJob();
          }
        };

        eventSource.onerror = (error) => {
          this.isSocketConnected = false;
          console.error("SSE SOCKET ERROR DUMP:", error);
          this.jobStatus = "Socket execution failed! readyState: " + eventSource.readyState;
          this.isJobRunning = false;
          eventSource.close();
        };

      },
      error: (err) => {
        console.error("Job Activation Failed:", err);
        this.isJobRunning = false;
        this.jobStatus = "Execution Error!";
      }
    });
  }

  deleteJob(jobId: number) {
    if (confirm(`Are you sure you want to permanently delete Job #${jobId} and all of its associated metadata?`)) {
      this.analysisService.deleteJob(jobId).subscribe({
        next: () => {
          this.jobsList = this.jobsList.filter(j => j.id !== jobId);
        },
        error: (err) => console.error("Failed to delete job", err)
      });
    }
  }

  reconnectJob(jobId: number) {
    this.jobId = jobId;
    this.showOrchestrator = true;
    this.isJobRunning = true;
    this.jobStatus = `Reconnecting to Job #${jobId}...`;
    
    const job = this.jobsList.find(j => j.id === jobId);
    this.startTime = job && job.startTime ? new Date(job.startTime).getTime() : Date.now();
    
    if (this.elapsedInt) clearInterval(this.elapsedInt);
    this.elapsedInt = setInterval(() => {
      const s = Math.floor((Date.now() - this.startTime) / 1000);
      this.elapsed = String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
    }, 500);

    // Bind Live SSE Source
    const eventSource = this.analysisService.streamAnalysis(this.jobId);
    
    eventSource.onopen = () => {
      this.isSocketConnected = true;
      this.jobStatus = `Target Schema: ${job ? job.targetSchema : 'Unknown'} · running...`;
    };
    
    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.logs.push({ time: this.ts(), stage: data.stage, msg: data.msg, cls: data.cls });
      
      this.syncStagesFromLog(data.stage, data.pct);
      
      if (data.stage === '[DONE]' || data.stage === '[ERROR]') {
        this.isSocketConnected = false;
        eventSource.close();
        this.finalizeJob();
      }
    };

    eventSource.onerror = (error) => {
      this.isSocketConnected = false;
      this.jobStatus = "Socket execution failed! readyState: " + eventSource.readyState;
      this.isJobRunning = false;
      eventSource.close();
    };
  }

  syncStagesFromLog(stg: string, pct?: number) {
    const stgIdxMap: any = { '[CRAWL]': 0, '[MATCH]': 1, '[CARD]': 2, '[PII]': 3, '[CLUST]': 4, '[GRAPH]': 5, '[CLASS]': 6 };
    if (stgIdxMap[stg] !== undefined) {
      const idx = stgIdxMap[stg];
      // Mark previous stages done
      for(let i=0; i<idx; i++) {
         this.stages[i].status = 'done';
         this.stages[i].pct = 100;
      }
      this.stages[idx].status = 'run';
      
      if (pct !== undefined && pct !== null) {
          this.stages[idx].pct = pct;
      } else {
          // Smooth pseudo-progress for fast blocking Python ML stages
          if (this.stages[idx].pct === 0) {
              this.stages[idx].pct = 20;
          } else if (this.stages[idx].pct < 90) {
              this.stages[idx].pct += 15;
          }
      }
      
      this.overallPct = Math.round(((idx) / 6) * 100) + Math.round((this.stages[idx].pct / 100) * (100 / 6));
    }
  }

  finalizeJob() {
    this.stages.forEach(s => { s.status = 'done'; s.pct = 100; });
    this.overallPct = 100;
    this.jobStatus = 'completed';
    if (this.elapsedInt) clearInterval(this.elapsedInt);
    this.showSummary = true;
    this.isJobRunning = false;
    this.jobCompleted = true;

    // Send complete audit upstream
    const serializedLogs = JSON.stringify(this.logs);
    this.analysisService.completeAnalysis(this.jobId, serializedLogs).subscribe();
  }

  viewResults() {
    this.router.navigate(['/dashboard']);
  }

  ts(): string {
    const d = new Date();
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, '0')).join(':');
  }
}
