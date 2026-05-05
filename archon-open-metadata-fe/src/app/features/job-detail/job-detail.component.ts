import { Component, Input, Output, EventEmitter, OnInit, ElementRef, ViewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { Network, Options, Data } from 'vis-network';
import { Job } from '../../core/models/app.models';
import { HttpClient } from '@angular/common/http';
import { ApiService } from '../../core/api.service';

@Component({
  selector: 'app-job-detail',
  templateUrl: './job-detail.component.html',
  styleUrls: ['./job-detail.component.css']
})
export class JobDetailComponent implements OnInit, AfterViewInit, OnDestroy {
  @Input() job: Job | undefined;
  @Output() back = new EventEmitter<void>();
  @ViewChild('networkContainer', { static: false }) networkContainer!: ElementRef;

  activeTab: string = 'tables';
  isFullScreen: boolean = false;

  tables: any[] = [];
  relationships: any[] = [];
  sensitiveData: any[] = [];
  dataGroups: any[] = [];

  networkInstance: Network | null = null;

  isSocketConnected: boolean = false;
  logs: {time: string, stage: string, msg: string, cls: string}[] = [];
  eventSource: EventSource | null = null;

  constructor(private http: HttpClient, private api: ApiService) {}

  ngOnInit() {
    if (this.job && (this.job.status === 'Pending' || this.job.status === 'Running')) {
      this.activeTab = 'console';
    }
    this.connectSse();
    if (this.job?.id) {
      this.fetchData();
    }
  }

  ngOnDestroy() {
    if (this.eventSource) {
      this.eventSource.close();
    }
    if (this.networkInstance) {
      this.networkInstance.destroy();
    }
  }

  toggleFullScreen() {
    this.isFullScreen = !this.isFullScreen;
  }

  fetchData() {
    if (!this.job?.id) return;
    
    // Fetch tables
    this.http.post<any>(`${this.api.baseUrl}/tables/search`, { jobId: this.job.id }).subscribe({
      next: (res) => {
        this.tables = res?._embedded?.tableEntityDtoList || [];
      },
      error: () => this.tables = []
    });

    // Fetch relationships
    this.api.getJobRelationships(this.job.id).subscribe({
      next: (res) => {
        if (res && res._embedded && res._embedded.relationshipDtoList) {
          this.relationships = res._embedded.relationshipDtoList;
        } else if (res && res.edges) {
          this.relationships = res.edges; // Fallback for old mock structure
        }
      },
      error: () => this.relationships = []
    });

    // Fetch sensitive data
    this.api.getJobSensitiveData(this.job.id).subscribe({
      next: (res) => {
        if (res && res._embedded && res._embedded.columnEntityDtoList) {
          this.sensitiveData = res._embedded.columnEntityDtoList;
        }
      },
      error: () => this.sensitiveData = []
    });

    // Fetch data groups
    this.api.getJobDataGroups(this.job.id).subscribe({
      next: (res) => {
        if (res && res._embedded && res._embedded.domainGroupDtoList) {
          this.dataGroups = res._embedded.domainGroupDtoList;
        }
      },
      error: () => this.dataGroups = []
    });
  }

  connectSse() {
    if (!this.job || !this.job.id) return;
    
    this.eventSource = new EventSource(`http://localhost:8080/api/v1/jobs/${this.job.id}/stream`);
    
    this.eventSource.onopen = () => {
      this.isSocketConnected = true;
    };
    
    this.eventSource.onerror = () => {
      this.isSocketConnected = false;
      if (this.eventSource) {
        this.eventSource.close();
      }
    };

    this.eventSource.addEventListener('log', (event: MessageEvent) => {
      this.logs.push({
        time: new Date().toLocaleTimeString(),
        stage: 'SYS',
        msg: event.data,
        cls: event.data.includes('ERROR') ? 'log-err' : 'log-info'
      });
    });

    this.eventSource.addEventListener('status', (event: MessageEvent) => {
      if (this.job) {
        this.job.status = event.data;
      }
      if (event.data === 'Done' || event.data === 'Failed') {
        setTimeout(() => {
           if (this.eventSource) this.eventSource.close();
           this.isSocketConnected = false;
        }, 1000);
      }
    });

    this.eventSource.addEventListener('stage', (event: MessageEvent) => {
      this.logs.push({
        time: new Date().toLocaleTimeString(),
        stage: 'STG',
        msg: 'Starting Stage: ' + event.data,
        cls: 'log-info'
      });
    });
  }

  ngAfterViewInit() {
  }

  setTab(tab: string) {
    this.activeTab = tab;
    if (tab === 'erd') {
      setTimeout(() => this.initGraph(), 100);
    }
  }

  initGraph() {
    if (!this.networkContainer) return;
    
    // For ERD graph, ideally fetch graph nodes/edges dynamically too.
    // For now we clear it if there's no data to avoid hardcoded fake nodes.
    const nodes: any[] = [];
    const edges: any[] = [];

    const data: Data = { nodes, edges };
    const options: Options = {
      autoResize: true,
      physics: {
        enabled: true,
        barnesHut: { gravitationalConstant: -12000, centralGravity: 0.2, springLength: 110, springConstant: 0.04, damping: 0.4 },
        stabilization: { iterations: 250 }
      },
      interaction: { hover: true, multiselect: false },
      nodes: { scaling: { min: 8, max: 28, label: { enabled: true, min: 12, max: 18 } }, borderWidth: 1 },
      edges: { color: '#94a3b8', smooth: false }
    };

    if (this.networkInstance) {
      this.networkInstance.destroy();
    }
    this.networkInstance = new Network(this.networkContainer.nativeElement, data, options);
    
    this.networkInstance.once('stabilizationIterationsDone', () => {
      this.networkInstance?.setOptions({ physics: { enabled: false } });
    });
  }
}
